"""ControllerLoop — the agentic chat-turn ReAct loop (mirrors PlanningLoop).

Reads always hit the real workspace (no shadow-read flip). Terminal actions own
their own teardown. E1 implements explore (tool_call) + answer; clarify/propose_mode/
edit/submit_changes are added in E2/E3.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentd.chat.edit_session import StaleWriteError
from agentd.chat.protected_paths import ProtectedPathError
from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.tool_events import trace_to_tool_events
from agentd.chat.turn_control import ChatTurnControl
from agentd.domain.models import AgentToolTrace, PatchFailureCode, ToolCall, ToolResult
from agentd.memory.harness import NO_OP_HARNESS, MemoryHarness
from agentd.memory.models import ObservedPrompt
from agentd.orchestrator.broadcaster import cap_event_output
from agentd.prompting.tagged import RenderContext, render_prompt, tagged
from agentd.providers.availability import ProviderUnavailable, is_provider_unavailable
from agentd.providers.plan_access import find_access_stop
from agentd.providers.usage import METER, USAGE_OWNER
from agentd.reasoning.react_common import (
    accepts_kwarg,
    assistant_turn,
    dedup_key,
    malformed_correction,
)
from agentd.skills.config import skills_body_max_chars
from agentd.subagents.framing import frame
from agentd.teams.tools import MAIN_TOOL_NAMES, MEMBER_TOOL_NAMES

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.chat.edit_session import TurnEditSession
    from agentd.domain.models import DiffEntry
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.reasoning.contracts import ReasoningEngine
    from agentd.subagents.context import AgentContext
    from agentd.subagents.inbox import InboxItem
    from agentd.tools.sources import AggregatingToolRegistry

    EditDecisionCb = Callable[[list[DiffEntry]], Awaitable[dict[str, object]]]
    # Persist+render an edit's resolution (diff, decision: "accept"|"reject", reason).
    # The controller owns the durable diff_card record + transcript broadcast; the
    # loop no longer broadcasts diff_ready itself (Class-A: the record cb is the one
    # writer, so every edit survives a reload — smoke-found gap #2/#5).
    # `was_gated` is per-edit, NOT the turn's starting preference: the review pref is
    # live-mutable (ChatTurnControl), so within one turn some edits gate and others
    # auto-accept. The breadcrumb decision keys off what actually happened.
    # decision is "accept" | "reject" | "stale" (a promote refused by the write guard, spec §7.4).
    EditRecordCb = Callable[[list[DiffEntry], str, str, bool], Awaitable[None]]
    # Given the files an accepted edit touched, return a compact retrieval-refresh
    # note (pointers only, no bodies) to append to history — or None.
    RetrievalDeltaCb = Callable[[list[str]], Awaitable[str | None]]
    # A team member's report fields (spec v2 §8.3): (report action, final) → verdict.
    ReportCheck = Callable[[dict[str, object], bool], "ReportVerdict"]
    # Persist the in-flight turn's pills incrementally (tool_events, thinking_log) so a
    # thread switch / panel reopen mid-turn reconstructs them durably (finding 5).
    PillsUpdateCb = Callable[[list[dict], list[str]], Awaitable[None]]


logger = logging.getLogger(__name__)

_MAIN = RenderContext.main()


# Loop-level retries for a provider outage, on top of the transport's own (spec §3.3).
# Some capacity errors carry no status code (NIM "ResourceExhausted ... (32/32)"), so the
# transport does not retry them; before v2 they self-healed only through the malformed path.
PROVIDER_RETRY_BACKOFFS_SEC: tuple[float, ...] = (15.0, 45.0)


class ControllerLoopExhausted(Exception):
    """Raised when the controller emits too many consecutive malformed responses.

    Mirrors PlanningLoop's PlanningBudgetExceededError malformed cap.
    """


# The modes propose_mode may offer; resolution routes on these exact strings
# (resolve_mode: "implement" re-enters the loop in ACTIVE; create_task/resume hand
# off). "implement" is a NEW value, not a relabeled "edit" — it collides with the
# unrelated `edit` action-type string ACTIVE's schema already uses if reused (see
# the design doc's I5 finding).
_VALID_MODES = frozenset({"implement", "create_task", "resume"})

# Tools that mutate the workspace. They are barred in the PLAN phase (read-only
# exploration/discussion before committing to act) so the model cannot write source
# files via the shell (`cat >`/`tee`/`touch`), bypassing the EditGate. Enforced at the
# dispatch guard only — the advertised tool list (system prompt) is unchanged, keeping
# the cached prefix byte-stable across the PLAN→ACTIVE transition.
_STATE_CHANGING_TOOLS = frozenset({"run_command"})

STATE_CHANGING_DECIDE_CORRECTION = (
    "run_command is not available in Plan Mode — it can mutate the workspace, which "
    "Plan Mode exists to discuss first. In this phase use only read-only tools "
    "(search_code / read_file / list_directory / read_env_profile). To make changes, "
    "emit propose_mode and let the user pick \"Implement this plan\"; run_command "
    "becomes available once you're out of Plan Mode."
)


def _permission_correction(
    resp: dict[str, object], atype: str, ctx: RenderContext,
) -> str | None:
    """Defense in depth for a read-only (effective `plan`) sub-agent (spec §5.6): its tool
    list already omits run_command and every mcp__ tool, so reaching this means the model
    named a tool it was never offered. Routed through the normal correction chain."""
    if ctx.is_main or ctx.permission != "plan" or atype != "tool_call":
        return None
    tool = str(resp.get("tool", ""))
    if tool == "run_command" or tool.startswith("mcp__"):
        return (f"You are read-only for this task: `{tool}` isn't available to you. "
                "Investigate with the read tools and put your findings in `report`.")
    return None


def _decide_state_change_correction(resp: dict[str, object], phase: str) -> str | None:
    """Reject a state-changing tool_call in PLAN; None otherwise (inert for other
    phases and non-tool_call actions)."""
    if phase != "PLAN" or str(resp.get("type", "")) != "tool_call":
        return None
    if str(resp.get("tool", "")) in _STATE_CHANGING_TOOLS:
        return STATE_CHANGING_DECIDE_CORRECTION
    return None


PROPOSE_MODE_CORRECTION = (
    "Your propose_mode was rejected: each option MUST be an object "
    '{"mode": <m>, "label": <short button text>, "description": <one line>} where '
    "<m> is one of implement | create_task | resume, and the top-level "
    '"recommended" MUST be one of those same values. You used an invalid mode name '
    "or the wrong keys (e.g. \"type\" instead of \"mode\"). Re-emit propose_mode with "
    "valid modes — typically offer \"implement\" (exit Plan Mode, make the change "
    "directly) at minimum, plus create_task (plan it as a reviewed task) when available."
)


def _propose_mode_correction(
    resp: dict[str, object], allowed_modes: frozenset[str] = _VALID_MODES
) -> str | None:
    """Return None if the propose_mode OPTIONS are well-formed AND every offered mode is in
    `allowed_modes`, else a correction.

    Enforces the mode vocabulary the same way the phase SM enforces action types: a weak model
    that invents modes ("create") or wrong keys (options[].type) gets corrected and retried
    rather than surfacing an unusable gate. `allowed_modes` is {implement} when the task
    subsystem is OFF (default) — so a model that offers create_task/resume despite the prompt
    omission gets corrected, not dispatched. `recommended` is a non-blocking hint — it's
    normalized in the emit branch, not required here (weak models reliably emit good options
    but drop the recommended field)."""
    options = resp.get("options")
    if not isinstance(options, list) or not options:
        return PROPOSE_MODE_CORRECTION
    for opt in options:
        if not isinstance(opt, dict) or opt.get("mode") not in allowed_modes:
            return PROPOSE_MODE_CORRECTION
    return None


def _empty_action_correction(resp: dict[str, object], atype: str) -> str | None:
    """Return a correction if a REQUIRED field for `atype` is empty, else None.

    The flat union schema (Gemini-compat, no oneOf) cannot enforce per-type required
    fields at the grammar level, so a weak model can satisfy it with a bare
    {"type":"answer"} (text dumped into the discarded 'thought') or a tool_call with no
    tool/args. Without this guard the loop returns an empty turn or executes "" — the
    empty-answer / empty-tool-call class. Treat these like a malformed action: correct +
    retry (bounded by the same _MAX_MALFORMED cap). submit_changes is NOT here — its empty
    summary is handled with a deterministic fallback in the emit branch (the edits are
    already done; retrying-to-exhaustion would discard real work)."""
    def _blank(key: str) -> bool:
        v = resp.get(key)
        return not (isinstance(v, str) and v.strip())

    if atype == "answer" and _blank("answer"):
        return (
            "Your 'answer' was empty. The COMPLETE response goes in the 'answer' field — "
            "'thought' is discarded. Re-emit type='answer' with a non-empty 'answer', or "
            "type='clarify' if you genuinely cannot answer."
        )
    if atype == "clarify" and _blank("question"):
        return "Your 'question' was empty. Re-emit type='clarify' with a concrete question."
    if atype == "progress" and _blank("note"):
        return (
            "Your 'note' was empty. Re-emit type='progress' with a short non-empty 'note' "
            "describing what you are doing, or take a real action instead."
        )
    if atype == "tool_call":
        if _blank("tool"):
            return (
                "Your tool_call had no 'tool'. Re-emit type='tool_call' with a tool name from "
                "AVAILABLE TOOLS and an 'args' object."
            )
        args = resp.get("args")
        if not isinstance(args, dict):
            return (
                f"Your tool_call for '{resp.get('tool')}' had no 'args' object. Re-emit with "
                'args as a JSON object (use {} if the tool takes no arguments, or '
                '{"path": ...} for read_file, {"pattern": ...} for search_code).'
            )
    if atype == "report" and _blank("summary"):
        return (
            "Your 'report' summary was empty. The COMPLETE report goes in 'summary' — it is the "
            "only thing your dispatcher receives. Re-emit type='report' with a non-empty 'summary'."
        )
    return None


# The top-level response `type`s (see CONTROLLER_RESPONSE_SCHEMA) — never real tool
# names. A model that just correctly used {"type":"tool_call","tool":"write_todos",...}
# can generalize the same shape onto these (most often 'edit'), since nothing in the
# tool_call schema constrains 'tool' to the registry. The registry's generic "unknown
# tool" error gives it no signal to self-correct from, so it can grind on the identical
# illegal call indefinitely (found live: an hour+ of retries, each a full regeneration).
# `progress` belongs here too (final whole-branch review, finding 3) — it is exactly as
# eligible for the {"type":"tool_call","tool":"progress",...} confusion as the others.
# The report statuses a lone sub-agent may choose (spec §3.3); a team member may also wait
# on a peer (spec v2 §7.3).
LONE_REPORT_STATUSES = frozenset({"completed", "partial"})
TEAM_REPORT_STATUSES = frozenset({"completed", "partial", "awaiting_peer"})

_RESERVED_ACTION_TOOL_NAMES = frozenset(
    {"answer", "clarify", "propose_mode", "edit", "submit_changes", "progress"})


_RESERVED_TOOL_NAME_TEMPLATE = tagged("reserved_tool_name", (
    "'{tool}' is not a callable tool — there is no such tool in AVAILABLE TOOLS. "
    "'{tool}' is a top-level response TYPE, emitted as its own object — "
    '{"type":"{tool}", ...} (see the "{tool}" variant above for its required '
    'fields) — NEVER as {"type":"tool_call","tool":"{tool}",...}.'
    "<<main>> If you are "
    "trying to make a change: type='edit' is already directly available — emit it "
    "now (Plan Mode is the only phase where you'd emit propose_mode first).<</main>>"
    "<<child:edit>> If you are trying to make a change: type='edit' is directly available — "
    "emit it now.<</child:edit>>"
    "<<child:readonly>> You are read-only for this task — finish with "
    "type='report'.<</child:readonly>>"
))


def _reserved_tool_name_correction(
    resp: dict[str, object], atype: str, ctx: RenderContext = _MAIN,
) -> str | None:
    """Reject a tool_call whose 'tool' is actually a top-level response type name."""
    if atype != "tool_call":
        return None
    tool = str(resp.get("tool", ""))
    reserved = (_RESERVED_ACTION_TOOL_NAMES if ctx.is_main
                else _RESERVED_ACTION_TOOL_NAMES | {"report"})
    if tool not in reserved:
        return None
    return render_prompt(_RESERVED_TOOL_NAME_TEMPLATE, ctx).replace("{tool}", tool)



_TOOL_AS_TYPE_TEMPLATE = tagged("tool_as_type", (
    "'{tool}' is a tool, not an action type. Call it with a tool_call: "
    '{"type":"tool_call","thought":"…","tool":"{tool}","args":{…}}'))


_ACTION_TYPES = frozenset({"answer", "clarify", "propose_mode", "edit", "submit_changes",
                           "tool_call", "progress", "report"})


def _unavailable_type_correction(
    atype: str, allowed: list[str], render_ctx: RenderContext | None,
) -> str | None:
    """A real action type that this iteration does not allow. Telling the model its output
    was empty (the generic correction) sent a deliberating team member back to `edit` again
    and again (found in the 5B live smoke)."""
    if atype not in _ACTION_TYPES:
        return None
    if allowed == ["report"]:
        return (f"'{atype}' is not available right now: your step budget is spent. Emit "
                "type='report' now with what you found and what is unfinished.")
    if atype == "edit" and render_ctx is not None and render_ctx.team_brief:
        return ("'edit' is not available right now: your team is not implementing, and files "
                "change only in implementation, by each file's owner. Use one of: "
                f"{', '.join(allowed)} — state your stance or propose the change, then report.")
    return f"'{atype}' is not available right now. Use one of: {', '.join(allowed)}."


def _tool_name_as_type_correction(
    atype: str, tool_names: set[str] | frozenset[str],
) -> str | None:
    """The reverse of _reserved_tool_name_correction (spec v2 §7.3): an action whose type is
    a team tool's name gets the tool_call wrapper shown, not the generic malformed text."""
    if atype not in tool_names or atype not in (MEMBER_TOOL_NAMES | MAIN_TOOL_NAMES):
        return None
    return render_prompt(_TOOL_AS_TYPE_TEMPLATE, _MAIN).replace("{tool}", atype)

# Guidance appended after a PARSE failure, chosen by the decoder's own complaint.
#
# One canned sentence used to answer every parse failure. Live on NIM that text
# happened to describe `Extra data` exactly (trailing prose after the object) and the
# model recovered on the very next call — while `Invalid control character` (a literal
# newline inside a string value) got the same words, which say nothing about it, and
# duly recurred. Same shape as the edit-guidance boilerplate fixed alongside this.
#
# Matched against json.JSONDecodeError's message text, which the transport now
# interpolates into the exception it raises.
_PARSE_GUIDANCE: tuple[tuple[str, str], ...] = (
    # FIRST on purpose. Truncation stops mid-object, so the decoder reports whatever
    # token it happened to land on — 'Unterminated string', "Expecting ','" — and any
    # of the symptom matchers below would otherwise claim it first and hand back
    # advice about quote escaping for JSON that was never malformed, only unfinished.
    # The transport only emits this marker when the provider itself said
    # finish_reason == "length", so it is a stated cause, not an inference.
    ("was TRUNCATED", (
        "Your response was cut off by the output token budget before the JSON was "
        "complete — the JSON you emitted was not malformed, there was simply no room "
        "left to finish it. Do NOT re-send the same thing: it will be cut at the same "
        "point. Emit a SMALLER response — write one file instead of several, split a "
        "large file across multiple edits in separate turns, or shorten the body you "
        "were about to write.")),
    ("Invalid control character", (
        "A string value contains a literal newline, tab or control character. Inside "
        "JSON these MUST be escaped as \\n and \\t — never written raw. This usually "
        "happens when a long multi-line answer or file body is placed in a string.")),
    ("Invalid \\escape", (
        "A lone backslash appears inside a string value. Every backslash in the CONTENT "
        "must be doubled for the JSON envelope: if the file you are writing contains "
        "\\n inside a Python string literal, a regex like \\d+, or a Windows path, it "
        "must appear as \\\\n / \\\\d+ in the JSON. A single \\ is only legal before "
        "\" \\ / b f n r t or u.")),
    ("Unterminated string", (
        "A string value is never closed. Check for an unescaped \" inside it — every "
        "double quote within a string must be written as \\\".")),
    ("Expecting ',' delimiter", (
        "A string value likely ended early because of an unescaped \" inside it — "
        "write every inner double quote as \\\".")),
    ("Extra data", (
        "Your JSON object was COMPLETE, but you kept generating after it. Stop "
        "immediately at the closing brace of the single object — do not continue with "
        "more fields, further messages, or anything else. Live evidence: on large "
        "responses the model carried on emitting conversation structure "
        "(\'\", {\"role\": \"tool\"…\') past the end of its own object. If the response is "
        "getting long, emit a SMALLER one (split a big file across turns) rather than "
        "running past the end.")),
    ("Expecting value", (
        "The response was empty or cut off before the JSON began. Reply with ONE "
        "complete JSON object.")),
)

_PARSE_GUIDANCE_FALLBACK = (
    "Respond again with EXACTLY ONE complete JSON object matching the schema — no "
    "prose, no markdown fences. If your response was cut off, produce a SHORTER one "
    "(e.g. a smaller file, or split a large change into more than one patch_ops entry "
    "across turns)."
)


def _parse_failure_guidance(exc_text: str) -> str:
    """Advice matched to WHY the JSON failed to parse, not a single canned line."""
    for marker, guidance in _PARSE_GUIDANCE:
        if marker in exc_text:
            return guidance
    return _PARSE_GUIDANCE_FALLBACK


# Guidance appended after a failed edit, chosen by the failure code.
#
# This used to be one frozen sentence (the _EDIT_GUIDANCE_FALLBACK below) sent for every
# failure. That sentence is advice for ONE malformation — code pasted into the 'file'
# field — and it was the dominant text the model saw for failures where 'file' was
# perfectly correct, pointing it at the wrong field. A live run showed 81% of edit
# failures were syntax errors, every one of them told to check its path handling.
#
# An empty string means "the message already says everything useful, add nothing".
_EDIT_GUIDANCE_BY_CODE: dict[PatchFailureCode, str] = {
    PatchFailureCode.ANCHOR_MISSING: (
        "The 'search' anchor was not found. Read the file first and copy the anchor "
        "text exactly as it appears, including indentation."),
    PatchFailureCode.ANCHOR_AMBIGUOUS: (
        "The 'search' anchor matched more than once. Include more surrounding lines "
        "so it identifies exactly one location."),
    PatchFailureCode.ORDER_CONFLICT: (
        "An earlier op in this same batch already changed that file, so this anchor no "
        "longer matches. Re-read the file and anchor against its current text."),
    PatchFailureCode.FILE_MISSING: (
        "That file does not exist. Use create_file to create it, or correct the path."),
    PatchFailureCode.FILE_EXISTS: (
        "That file already exists. Use search_replace or apply_diff to change it "
        "instead of create_file."),
    PatchFailureCode.APPLY_ERROR: (
        "The patched file does not parse. Fix the syntax at the reported line — the "
        "usual cause is an unterminated string or an unbalanced bracket in the content "
        "you emitted."),
    PatchFailureCode.SCOPE_VIOLATION: (
        "That file is outside the current scope."),
    PatchFailureCode.PATH_ESCAPE: (
        "'file' must be a workspace-relative path inside the workspace."),
    PatchFailureCode.STALE_READ: (
        "This file changed after your last read (another agent, the user or a tool). "
        "read_file it again, then re-emit your edit against its current content."),
    PatchFailureCode.PROTECTED_PATH: (
        "That file is protected. Describe the change in your report or answer instead "
        "of editing it."),
    PatchFailureCode.TEAM_SCOPE: (
        "A team's plan decides who edits which file. Ask the file's owner instead of editing "
        "it: a member uses team_message (or team_post to reach the main agent); the main "
        "agent uses post_board mentioning the owner."),
    PatchFailureCode.NO_OP: (
        "Your edit changes nothing: the file already has exactly that content (for "
        "search_replace, 'replace' is identical to 'search'). Emit an edit that makes the "
        "change; if the change is already in the file, don't edit it again."),
}

# Kept for every failure we cannot classify — notably the malformed-op shape errors
# raised by _validate_patch_ops before preflight ever runs, which is what it was
# written for.
_EDIT_GUIDANCE_FALLBACK = (
    "Re-emit ONE corrected edit op — 'file' is a workspace-relative path, "
    "code goes in 'content'.")


def _edit_failure_guidance(exc: Exception) -> str:
    """Guidance matched to why the edit actually failed.

    Uses the FIRST issue, which is the one _format_preflight_issues leads with, so the
    prose and the message agree about which op they are talking about.
    """
    issues = getattr(exc, "issues", None)
    if not issues:
        return _EDIT_GUIDANCE_FALLBACK
    return _EDIT_GUIDANCE_BY_CODE.get(issues[0].code, _EDIT_GUIDANCE_FALLBACK)


# A forward-looking, first-person intent phrase — "I'm about to do X" rather than
# "I already did X". Paired below with a real tool name so the correction only fires
# on the specific "announced but not taken" shape (bare tool-name mentions in an
# otherwise-finished explanatory answer are common and must NOT trip this).
_FORWARD_INTENT_RE = re.compile(
    r"\b(let me start|let'?s start|i'?ll begin|i will begin|i'?m going to|"
    r"i should (?:use|call|run|invoke)|i need to (?:use|call|run|invoke)|"
    r"before i can\b|next i(?:'?ll| will| need to))\b",
    re.IGNORECASE,
)


def _answer_intent_divergence_correction(
    resp: dict[str, object], atype: str, tool_names: frozenset[str],
) -> str | None:
    """Reject an 'answer' whose own thought/answer narrates a concrete next tool call
    it has not taken — the "announce, don't act" failure. CONTROLLER_SYSTEM_PROMPT
    already teaches this in prose (WRONG/RIGHT examples on the answer variant); a live
    dogfood run reproduced it twice regardless (right after a plan-writing sub-goal
    landed, the model's own thought said "I should use write_todos first... then start
    implementing" and STILL emitted type='answer' narrating intent — see
    docs/superpowers/... skill-handoff-gap write-up). This is a mechanical backstop for
    that specific shape, not a replacement for the prompt guidance: it only fires when
    BOTH a forward-looking first-person intent phrase AND a real tool name from
    AVAILABLE TOOLS appear together in thought+answer — narrower than either signal
    alone, to keep false positives on ordinary explanatory answers low."""
    if atype != "answer":
        return None
    combined = f"{resp.get('thought', '')} {resp.get('answer', '')}"
    if not _FORWARD_INTENT_RE.search(combined):
        return None
    lowered = combined.lower()
    mentioned = next((t for t in tool_names if t and t.lower() in lowered), None)
    if mentioned is None:
        return None
    return (
        f"Your own thought/answer describes a next step you have not taken ('{mentioned}' "
        f"mentioned alongside forward-looking language like \"I should\"/\"let me start\"). "
        "'answer' ENDS the turn — nothing you described will actually run, and the user will "
        f"have to prompt you again just to get you to do what you already said. If '{mentioned}' "
        "(or whatever the real next action is) is genuinely next, TAKE it now: emit "
        "type='tool_call' with that tool (or type='edit'/'submit_changes' if that's the real "
        "next action) instead of narrating it in 'answer'. If you only want to TELL the user "
        "what you are about to do without ending the turn, emit type='progress' with a 'note', "
        "then take the action."
    )


# A progress note is a short user-visible status line, not a place to dump work — cap
# it so a model that decides to narrate at length can't bloat the transcript or history.
_PROGRESS_NOTE_MAX_CHARS = 500


def _normalize_progress_note(resp: dict[str, object]) -> str:
    """The ONE canonical form of a progress note — stripped and capped.

    Both the dedup guard and the dispatch branch must derive the note through this, so
    they compare and store the same bytes: normalizing at dispatch only (storing the
    capped note) while checking the raw value let any note longer than the cap evade
    dedup entirely, since it could never equal what was stored.
    """
    return str(resp.get("note", "")).strip()[:_PROGRESS_NOTE_MAX_CHARS]


_PROGRESS_REPEAT_TEMPLATE = tagged("progress_repeat", (
    "You already posted a progress note and took no action after it. A progress note "
    "does NOT count as doing the work. Take the actual next action now — emit "
    "type='tool_call'<<type:edit>> / 'edit'<</type:edit>><<main>> / 'submit_changes' (or 'answer' "
    "if you are truly done)<</main>><<child>> (or 'report' if you are done)<</child>>."
))
_PROGRESS_DEDUP_TEMPLATE = tagged("progress_dedup", (
    "You already posted that exact progress note this turn. Do not repeat it — "
    "take the next real action instead (tool_call<<type:edit>> / edit<</type:edit>>"
    "<<main>> / submit_changes / answer<</main>><<child>> / report<</child>>)."
))


def _progress_repeat_correction(
    resp: dict[str, object], atype: str, last_was_progress: bool, ctx: RenderContext = _MAIN,
) -> str | None:
    """Reject a `progress` note that immediately follows another `progress` with no real
    action between — a weak model can turn a free non-terminal action into a narration
    attractor (spam notes instead of acting). Mirrors the emit_patch-dedup discipline;
    routes through the same _MAX_MALFORMED correction chain (no new retry primitive)."""
    if atype != "progress" or not last_was_progress:
        return None
    return render_prompt(_PROGRESS_REPEAT_TEMPLATE, ctx)


def _progress_dedup_correction(
    resp: dict[str, object], atype: str, seen_notes: set[str], ctx: RenderContext = _MAIN,
) -> str | None:
    """Reject an exact-duplicate progress note already emitted this turn (same attractor
    class as _progress_repeat_correction, but catches non-adjacent repeats)."""
    if atype != "progress":
        return None
    note = _normalize_progress_note(resp)
    # An empty note is _empty_action_correction's business (it fires earlier in the
    # chain); never let "" match a stored value here.
    if note and note in seen_notes:
        return render_prompt(_PROGRESS_DEDUP_TEMPLATE, ctx)
    return None


_EMPTY_EDIT_REDIRECT_TEMPLATE = tagged("empty_edit_redirect", (
    "That 'edit' had no patch_ops, so NOTHING was applied. If you meant to "
    "create or update the TODO LIST, that is a TOOL CALL — emit "
    '{"type":"tool_call","tool":"write_todos","args":{"items":[…]}}, NOT '
    "type='edit'. To change a file, emit type='edit' with a NON-EMPTY "
    "patch_ops (each op: file + its op fields). To finish, emit "
    "type='<<main>>submit_changes<</main>><<child>>report<</child>>'."
))


def _empty_edit_redirect(ctx: RenderContext) -> str:
    return render_prompt(_EMPTY_EDIT_REDIRECT_TEMPLATE, ctx)


# Matches this workspace's writing-plans skill's own plan-doc convention, e.g.:
#   "REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
#    or superpowers:executing-plans to implement this plan task-by-task."
# One directive LINE can name more than one candidate (an "either/or" handoff choice,
# as above) — _REQUIRED_SUBSKILL_LINE_RE captures the rest of that line, then
# _SUPERPOWERS_NAME_RE pulls every `superpowers:<name>` mention out of it, so
# _extract_required_subskills returns all of them, in the order they appear.
_REQUIRED_SUBSKILL_LINE_RE = re.compile(r"REQUIRED SUB-SKILL:([^\n]*)", re.IGNORECASE)
_SUPERPOWERS_NAME_RE = re.compile(r"superpowers:([a-z0-9_-]+)", re.IGNORECASE)

# A plan directive naming "subagent-driven-development" needs a sub-agent dispatch tool.
# This host has one (dispatch_agents) only while CRUCIBLE_SUBAGENTS_ENABLED is on; with it
# off, only its sibling "executing-plans" (one continuous session) is runnable here. A fact
# about THIS host's tools, not a preference between the two skills (spec §16 phase 3).
_SUBAGENT_ONLY_SUBSKILLS = frozenset({"subagent-driven-development"})


def _non_executable_subskills() -> frozenset[str]:
    from agentd.chat.controller_factory import is_subagents_enabled

    return frozenset() if is_subagents_enabled() else _SUBAGENT_ONLY_SUBSKILLS


def _extract_required_subskills(ops: list[dict[str, object]]) -> list[str]:
    """Pull every `superpowers:<name>` named in a 'REQUIRED SUB-SKILL:' directive line
    out of a just-applied edit's raw op text (content/diff/replace/search — whichever
    fields that op type carries). Order-preserving, de-duplicated across all ops in the
    batch."""
    seen: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        text = " ".join(
            str(op.get(k, "")) for k in ("content", "diff", "replace", "search")
        )
        for line_match in _REQUIRED_SUBSKILL_LINE_RE.finditer(text):
            for name_match in _SUPERPOWERS_NAME_RE.finditer(line_match.group(1)):
                name = name_match.group(1)
                if name not in seen:
                    seen.append(name)
    return seen


def _pick_executable_required_subskill(names: list[str]) -> str | None:
    """Of the names a plan directive names, return the one THIS host can actually run
    (see _non_executable_subskills()) — None if the directive named none, or named only
    ones this host can't run."""
    blocked = _non_executable_subskills()
    return next((n for n in names if n not in blocked), None)


def _normalized_recommended(resp: dict[str, object]) -> str:
    """The model's recommended mode if valid, else the first option's mode (a hint,
    never blocks the gate — see _propose_mode_correction)."""
    rec = resp.get("recommended")
    if rec in _VALID_MODES:
        return str(rec)
    options = resp.get("options")
    if isinstance(options, list) and options and isinstance(options[0], dict):
        return str(options[0].get("mode", ""))
    return ""


@dataclass(frozen=True)
class ReportVerdict:
    """What a team member's report fields came to (spec v2 §8.3). `message` None accepts
    the report; otherwise it is shown to the model, which reports again. `malformed` marks
    a byte-identical resubmission of a refused report, counted like any malformed action."""
    message: str | None = None
    malformed: bool = False


@dataclass
class ControllerOutcome:
    # "answer" | "clarify" | "propose_mode" | "submit_changes" | "report" (a sub-agent's
    # terminal; its payload is {"status": "completed" | "partial"}).
    kind: str
    text: str = ""
    payload: dict[str, object] | None = None
    history: list[dict[str, object]] | None = None
    # Durable tool pills (ToolEventView shape) for the turn — persisted onto the
    # agent message so they survive a reload (live SSE pills die). None until the
    # loop finalizes it in run().
    tool_events: list[dict[str, object]] | None = None
    # Durable thinking entries (tool labels) for the turn — persisted alongside the
    # pills so the ThinkingBlock reconstructs on reload (mirrors agent.py/ToolLoop).
    thinking_log: list[str] | None = None


class ControllerLoop:
    def __init__(
        self,
        reasoning: ReasoningEngine,
        registry: AggregatingToolRegistry,
        broadcaster: EventBroadcaster,
        *,
        channel_id: str,
        phase_sm: ControllerPhaseSM,
        edit_session_factory: Callable[[], TurnEditSession] | None = None,
        todo_ledger: TodoLedger | None = None,
        task_subsystem_enabled: bool = False,
        memory_harness: MemoryHarness = NO_OP_HARNESS,
        active_skills: dict[str, str] | None = None,
        skill_catalog_loader: object | None = None,
        active_skill_persist_cb: Callable[[str | None], Awaitable[None]] | None = None,
        progress_note_cb: Callable[[str], Awaitable[None]] | None = None,
        pills_seal_cb: Callable[[], None] | None = None,
        render_ctx: RenderContext | None = None,
        agent: AgentContext | None = None,
        report_statuses: frozenset[str] = LONE_REPORT_STATUSES,
    ) -> None:
        self._reasoning = reasoning
        self._registry = registry
        self._broadcaster = broadcaster
        self._channel_id = channel_id
        self._sm = phase_sm
        # Built lazily on first `edit` dispatch (see the edit branch in _iterate), not
        # eagerly here — ACTIVE is now the default phase for every plain turn, so
        # eager construction would build a (cheap but real) session + shadow for every
        # pure Q&A turn too. The factory itself is a free closure either way.
        self._edit_session_factory = edit_session_factory
        self._edit: TurnEditSession | None = None
        self._ledger = todo_ledger or TodoLedger()
        self._memory_harness = memory_harness
        # Shared with the SkillToolSource: read_skill writes activated bodies here; each
        # iteration we re-inject them into the dynamic tail (compaction-resilient).
        self._active_skills = active_skills if active_skills is not None else {}
        # Duck-typed SkillCatalogLoader (mirrors tool_source.py's own `loader: object`
        # typing — avoids an import-cycle-prone hard dependency here). None unless
        # skills are enabled, in which case _maybe_force_required_subskill can resolve
        # a REQUIRED SUB-SKILL directive name to its body without the model calling
        # read_skill itself.
        self._skill_catalog_loader = skill_catalog_loader
        self._active_skill_persist_cb = active_skill_persist_cb
        # Persists a non-terminal `progress` note as a durable transcript message. The
        # loop broadcasts the live chat_progress event itself; this cb is the durable
        # half (reload). None → live-only (broadcast still fires).
        self._progress_note_cb = progress_note_cb
        self._task_subsystem_enabled = task_subsystem_enabled
        # PLAN's allowed modes: the full vocabulary when the task subsystem is on,
        # else just "implement" (create_task/resume stripped — the controller
        # handles everything inline).
        self._plan_allowed_modes = (
            _VALID_MODES if task_subsystem_enabled else frozenset({"implement"}))
        # ACTIVE's allowed modes (I4): only reachable at all when the task subsystem
        # is on, and even then restricted to create_task/resume — never "implement",
        # since ACTIVE is already the implementing phase.
        self._active_allowed_modes = frozenset({"create_task", "resume"})
        self._calls: list[ToolCall] = []
        self._results: list[ToolResult] = []
        self._thinking: list[str] = []
        # Index into self._calls/self._results/self._thinking marking the start of the
        # CURRENT pills segment — advanced whenever a durable message lands in the
        # transcript mid-turn (a `progress` note, a gate breadcrumb, an inert diff_card).
        # Such a message is appended at the END of the message list, while pills
        # accumulate in ONE in-flight message updated in place (ChatThreadStore.
        # upsert_inflight_pills) — so without a boundary every later pill, and the
        # closing text that finalizes that same message, is persisted BEHIND something
        # that happened before it (on reload the whole turn's pills pile up at the top,
        # with only the breadcrumbs in position). These two indices are the LOOP-side
        # half of the boundary: they keep every pills computation (live call_index + the
        # durable tool_events/thinking_log passed to on_pills_update, and the final
        # outcome's own tool_events/thinking_log) scoped to "since the last boundary"
        # instead of the whole turn, so a later segment never re-shows pills an
        # already-sealed message carries. The store-side half is _pills_seal_cb. Both
        # stay untouched for a turn with no mid-turn message, so that turn is
        # byte-identical to pre-fix behavior (slicing a list from 0 is the full list).
        self._pill_segment_start = 0
        self._thinking_segment_start = 0
        # Deferred boundary (see mark_pills_boundary/_apply_pills_boundary). A gate
        # callback fires DURING registry.execute(), i.e. mid-tool-call, so applying the
        # boundary there would strand the in-flight call: its live `call_index` was
        # already broadcast relative to the OLD segment, and the pill it produces belongs
        # with the ones the live webview sealed alongside it. Marking now and applying at
        # the next iteration boundary keeps live and durable order identical.
        self._pills_boundary_pending = False
        # Seals the store's in-flight pills message (drops its inflight_turn_id marker so
        # it freezes in place and the next tool result appends a fresh one). None → the
        # loop still segments its own pill arrays, it just has nothing to seal.
        self._pills_seal_cb = pills_seal_cb
        # The live conversation list `_iterate` mutates — exposed via partial_history() so a
        # caller can persist what a CANCELLED turn (/stop) accumulated before the cancel
        # raised, instead of losing the turn's exploration + already-promoted edits (Q2).
        self._history: list[dict[str, object]] = []
        # Whether any edit has been applied this turn — half of the `active_entry` signal
        # (the other half is "no todo list yet"). While both hold, the payload builder shows
        # the clean entry hint (write_todos-as-tool_call) instead of the mid-turn reconcile
        # hint, so the first-action case isn't mis-routed.
        self._edit_applied = False
        # Work this turn handed off: teams it created and agents it dispatched, and whether
        # wait_agents has collected their reports. Set from successful tool results, it ends
        # the entry hint ("nothing is started yet") and selects the delegated hint —
        # without it a model that delegated was told every call to start the work again
        # (live 2026-10-07, gpt-5.6-luna: it re-dispatched a team's whole job).
        self._delegated_teams: list[str] = []
        self._delegated_agents: list[str] = []
        self._agents_collected = False
        # The provider's exact size for the last call this loop made, pinned to the
        # history length it measured (see ObservedPrompt). Seeded from run()'s
        # observed_prompt param — the caller's carried cross-turn state — updated in
        # place by _on_usage every call, and invalidated (None) whenever compaction
        # rewrites history. `history` is NOT turn-scoped (ChatController._seed_for
        # replays the whole prior conversation as seed_history), so a purely
        # run()-local value made the exact-accounting circuit a no-op on ordinary
        # multi-turn chat: every turn's first iteration decided with observed=None on
        # the largest history the thread had ever had (final whole-branch review,
        # finding 1). partial_observed_prompt() exposes this after run() returns
        # (any exit path) so ChatController can carry it into the NEXT turn the same
        # way it already threads _histories.
        self._observed_prompt: ObservedPrompt | None = None
        # Which audience this loop's text is rendered for (spec §4.7). None = the main agent,
        # whose every string stays byte-identical (tests/test_prompt_goldens.py).
        self._render_ctx = render_ctx or RenderContext.main()
        # A dispatched sub-agent (spec §5.1). None = the main agent, whose behavior is
        # unchanged by everything keyed off this.
        self._agent = agent
        # A chosen status outside this set reports as "completed".
        self._report_statuses = report_statuses
        # Set per iteration by _iterate; the final iteration narrows a child's types.
        self._iteration = 0
        self._force_final = False
        self._max_iters = 0

    def mark_pills_boundary(self) -> None:
        """A durable message just landed in the transcript mid-turn — close the current
        pills segment at the next iteration boundary.

        Called by the loop itself for a `progress` note, and by ChatController for every
        other mid-turn durable write (gate breadcrumbs, inert diff_cards) via
        _mark_pills_boundary. Idempotent: repeated marks before an apply collapse into
        one boundary, which is what a burst of breadcrumbs with no pill between them
        should produce (an empty segment would persist an empty pills message)."""
        self._pills_boundary_pending = True

    def _apply_pills_boundary(self) -> None:
        """Apply a pending boundary: seal the store's in-flight pills message where it is,
        and scope all subsequent pill/thinking slices to what comes after it.

        Deliberately deferred to an iteration boundary rather than done at mark time —
        see self._pills_boundary_pending. Best-effort on the store half: a persist failure
        must never break the turn (mirrors the on_pills_update call site)."""
        if not self._pills_boundary_pending:
            return
        self._pills_boundary_pending = False
        if self._pills_seal_cb is not None:
            try:
                self._pills_seal_cb()
            except Exception:
                logger.debug("[controller] in-flight pill seal failed", exc_info=True)
        self._pill_segment_start = len(self._calls)
        self._thinking_segment_start = len(self._thinking)

    async def _maybe_force_required_subskill(
        self, ops: list[dict[str, object]], history: list[dict[str, object]],
    ) -> None:
        """Deterministically load a skill a just-applied edit's own text says is
        REQUIRED — a judgment gap the skill-catalog prompt's model-driven read_skill
        cannot be relied on to close on its own.

        Root cause (confirmed live, 2 independent dogfood runs): writing-plans'
        template makes the model author a plan document whose OWN header names
        "REQUIRED SUB-SKILL: superpowers:executing-plans" (or
        subagent-driven-development), then the model continues executing that same
        plan later in the SAME turn — well past `skill_check_due`, which is
        deliberately one-shot-per-turn by design (see
        docs/superpowers/specs/2026-07-21-merged-decide-edit-phase-design.md: "skill
        triage is genuinely redundant to re-run every iteration once it's been done or
        missed" — true for the turn-start case that spec covers, but this trigger is a
        DIFFERENT one: new information the model itself just wrote, mid-turn). No
        prompt nudge fired here in either observed run. Since the plan text already
        names the exact skill, there's nothing left to infer — force-load it the same
        way /skill's forced_skills seeding does, rather than iterating on prompt
        wording again (P2's skill-judgment work took 3 rounds for a similar gap and
        even then only reached "promising, not proven at scale" — see
        project_p2_agent_skills memory).
        """
        if self._skill_catalog_loader is None:
            return
        names = _extract_required_subskills(ops)
        if not names:
            return
        target = _pick_executable_required_subskill(names)
        if target is None or target in self._active_skills:
            return
        catalog = self._skill_catalog_loader.load_catalog()  # type: ignore[attr-defined]
        manifest = next((m for m in catalog if m.name == target), None)
        if manifest is None:
            return
        try:
            body = manifest.body_path.read_text(encoding="utf-8")
        except OSError:
            return
        cap = skills_body_max_chars()
        if len(body) > cap:
            body = body[:cap] + f"\n\n[... skill '{target}' truncated at {cap} chars ...]"
        # Mirrors SkillToolSource.execute's "exactly one skill active" replace semantics.
        self._active_skills.clear()
        self._active_skills[target] = body
        if self._active_skill_persist_cb is not None:
            await self._active_skill_persist_cb(json.dumps({"name": target, "body": body}))
        logger.info(
            "[controller] auto-loaded required sub-skill %r (named in a plan doc "
            "written this turn)", target)
        self._thinking.append(
            f"⚡ auto-loaded skill '{target}' — required by the plan document you just wrote")
        # A synthetic tool_result (no real read_skill call happened) so the model sees
        # WHY the skill body appeared in its payload and is told to act on it now,
        # instead of silently mutating context the model has no visibility into.
        history.append({
            "role": "tool_result", "tool": "read_skill",
            "content": (
                f"[auto-loaded] The document you just wrote names "
                f"REQUIRED SUB-SKILL: superpowers:{target}. Its instructions are now "
                "active (see active_skills in your payload) — follow them starting "
                "with their own first step, in THIS same turn. Do not re-call "
                "read_skill for it."
            ),
        })

    def _allowed_action_types(self) -> list[str]:
        """The action types legal THIS iteration. The main agent: the phase SM's own set,
        plus a conditional propose_mode addition when in ACTIVE with the task subsystem on
        (I4: lets a big-enough ACTIVE-phase request escalate to a reviewed task without a
        detour through Plan Mode). A sub-agent: its own type set, narrowed to `report` on
        the final iteration so the budget always ends in a report (spec §6.5) — the system
        prompt keeps the full base set, only the schema narrows."""
        if self._agent is not None:
            if self._iteration >= self._max_iters or self._force_final:
                return ["report"]
            return list(self._agent.allowed_types)
        types = list(self._sm.allowed_types())
        if self._sm.phase == "ACTIVE" and self._task_subsystem_enabled:
            types.append("propose_mode")
        return types

    def force_final(self) -> None:
        """Make the next iteration the final one (spec v2 §8.3 deadline, §3.11 budget): the
        schema narrows to report, and the report is accepted as it stands."""
        self._force_final = True

    def _allowed_modes_for_current_phase(self) -> frozenset[str]:
        return (
            self._active_allowed_modes if self._sm.phase == "ACTIVE"
            else self._plan_allowed_modes)

    def fallback_report(
        self, error: str, files_changed: list[str], *, status: str = "failed",
    ) -> str:
        """The report for a sub-agent that produced none (malformed exhaustion, a crash,
        or a stop — spec §6.5, §11.4). Synthesized from what the loop recorded — never a
        truncation of anything the model wrote."""
        lines = [f"Status: {status} — {error}", "",
                 "Files changed: " + (", ".join(files_changed) or "none")]
        still_open = self._ledger.pending()
        if still_open:
            lines += ["", "Unfinished:", *(f"- {i.title} ({i.status})" for i in still_open)]
        recent = self._calls[-10:]
        if recent:
            lines += ["", "Last tool calls:", *(
                f"- {c.tool_name} {json.dumps(c.arguments, sort_keys=True)}" for c in recent)]
        return "\n".join(lines)

    @property
    def tool_calls(self) -> list[ToolCall]:
        """The tool calls recorded so far (a copy) — what /live's roster reads (§11.1)."""
        return list(self._calls)

    def partial_history(self) -> list[dict[str, object]]:
        """The verbatim conversation accumulated so far this turn. Meaningful after a
        cancel: run()'s normal return persists history itself, but a CancelledError unwinds
        before that — the caller reads this to persist the partial."""
        return self._history

    def partial_observed_prompt(self) -> ObservedPrompt | None:
        """The loop's carried prompt-size observation as of right now.

        Valid whether the turn completed normally, raised, or was cancelled — unlike
        partial_history() this is read on EVERY exit path (not only the unwind cases),
        since it is simpler for the caller to read one accessor after run() than to
        thread the value through every ControllerOutcome construction site."""
        return self._observed_prompt

    def _note_delegation(self, tool: str, args: object) -> None:
        """Record work a successful tool call handed off (see _delegated_teams)."""
        fields = args if isinstance(args, dict) else {}
        if tool == "create_team":
            name = str(fields.get("name") or "the team")
            if name not in self._delegated_teams:
                self._delegated_teams.append(name)
        elif tool == "dispatch_agents":
            agents = fields.get("agents")
            for entry in agents if isinstance(agents, list) else []:
                label = str(entry.get("label") or entry.get("agent") or "agent") if isinstance(
                    entry, dict) else "agent"
                if label not in self._delegated_agents:
                    self._delegated_agents.append(label)
            self._agents_collected = False
        elif tool == "wait_agents" and self._delegated_agents:
            self._agents_collected = True

    def _delegation(self) -> dict[str, object] | None:
        """What this turn handed off, for the per-turn hint — None when nothing was, or
        once the turn applied an edit itself (the edit hints take over then)."""
        if self._edit_applied or not (self._delegated_teams or self._delegated_agents):
            return None
        return {"teams": list(self._delegated_teams), "agents": list(self._delegated_agents),
                "agents_collected": self._agents_collected}

    async def run(
        self,
        plan_context: dict[str, object],
        *,
        max_iters: int = 32,
        seed_history: list[dict[str, object]] | None = None,
        observed_prompt: ObservedPrompt | None = None,
        auto_accept_edits: bool = False,
        turn_control: ChatTurnControl | None = None,
        edit_decision_cb: EditDecisionCb | None = None,
        edit_record_cb: EditRecordCb | None = None,
        retrieval_delta_cb: RetrievalDeltaCb | None = None,
        on_pills_update: PillsUpdateCb | None = None,
        iteration_cb: Callable[[list[dict[str, object]]], None] | None = None,
        inbox_drain: Callable[[], list[InboxItem]] | None = None,
        report_guard: Callable[[], str | None] | None = None,
        terminal_guard: Callable[[], str | None] | None = None,
        status_tail: Callable[[], str] | None = None,
        final_hint: Callable[[], str] | None = None,
        report_check: ReportCheck | None = None,
        live_work: Callable[[], dict[str, object] | None] | None = None,
    ) -> ControllerOutcome:
        tool_defs = [d.model_dump() for d in self._registry.definitions()]
        history = [dict(m) for m in seed_history] if seed_history else []
        # Expose the live list NOW (before _iterate can raise) so a cancel mid-turn still
        # leaves the caller a readable partial (Q2). _iterate mutates this same object.
        self._history = history
        # Seed with the caller's carried observation (prefix-valid: seed_history is
        # last turn's history plus one appended message, and message_count only ever
        # points at a prefix of it) — see partial_observed_prompt().
        self._observed_prompt = observed_prompt
        seen: dict[str, int] = {}
        # Bail only after this many CONSECUTIVE malformed responses (mirror PlanningLoop).
        _MAX_MALFORMED = 3
        consecutive_malformed = 0
        plan_context = {**plan_context, "max_iters": max_iters}
        self._max_iters = max_iters
        if self._agent is not None:
            # The Plan 1A AGENT payload branch reads this for its read-only hints.
            plan_context["agent_readonly"] = self._agent.permission == "plan"
        if self._agent is None and any(
                d.get("name") == "dispatch_agents" for d in tool_defs):
            # The parent's entry hint offers parallel dispatch (spec §6.6).
            plan_context["dispatch_available"] = True
        # Tool trace + thinking accumulated across the turn → persisted as durable
        # pills + thinking entries (reload). Live SSE copies die on reload.
        self._calls = []
        self._results = []
        self._thinking = []
        self._pill_segment_start = 0
        self._thinking_segment_start = 0
        self._pills_boundary_pending = False
        self._edit_applied = False
        self._delegated_teams, self._delegated_agents = [], []
        self._agents_collected = False
        try:
            outcome = await self._iterate(
                plan_context, history, tool_defs, seen, max_iters,
                _MAX_MALFORMED, consecutive_malformed,
                # `auto_accept_edits` is only the STARTING value: callers that want the
                # preference to stay live for the turn hand in a control instead, and the
                # edit dispatch re-reads it. (Same fallback shape as engine.py's
                # `_ctrl.step_review_auto_accept if _ctrl is not None else task....`)
                turn_control=turn_control or ChatTurnControl(
                    auto_accept_edits=auto_accept_edits),
                edit_decision_cb=edit_decision_cb,
                edit_record_cb=edit_record_cb,
                retrieval_delta_cb=retrieval_delta_cb,
                on_pills_update=on_pills_update,
                iteration_cb=iteration_cb,
                inbox_drain=inbox_drain,
                report_guard=report_guard,
                terminal_guard=terminal_guard,
                status_tail=status_tail,
                final_hint=final_hint,
                report_check=report_check,
                live_work=live_work,
            )
            if iteration_cb is not None:
                iteration_cb(history)
            # The terminal action is itself an iteration, so a boundary marked during it
            # (or during the tool call before it) has had no iteration top to run at —
            # apply it here, BEFORE slicing, or the closing message would finalize the
            # in-flight message created ahead of that mid-turn write and land behind it.
            self._apply_pills_boundary()
            # Sliced to the segment since the last mid-turn durable message — one right
            # before the terminal action correctly yields an EMPTY segment here
            # (segment_start == len(self._calls)), since the seal froze everything
            # preceding it into its own durable message; the closing message should only
            # carry pills from AFTER the boundary, not repeat them. A turn with no
            # mid-turn message has segment_start==0 for its whole life, so this stays
            # byte-identical to the pre-fix "whole self._calls" behavior.
            segment_calls = self._calls[self._pill_segment_start:]
            segment_results = self._results[self._pill_segment_start:]
            segment_thinking = self._thinking[self._thinking_segment_start:]
            if outcome.tool_events is None and segment_calls:
                outcome.tool_events = trace_to_tool_events(
                    AgentToolTrace(step_id="chat", calls=segment_calls, results=segment_results),
                    "execution")
            if outcome.thinking_log is None and segment_thinking:
                outcome.thinking_log = list(segment_thinking)
            return outcome
        finally:
            # The per-turn shadow is discarded at turn end on ANY exit (submit, budget
            # exhaustion, exhaustion-raise, or a patch crash) — no shadow leak.
            if self._edit is not None:
                await self._edit.close()

    async def _iterate(
        self,
        plan_context: dict[str, object],
        history: list[dict[str, object]],
        tool_defs: list[dict[str, object]],
        seen: dict[str, int],
        max_iters: int,
        _MAX_MALFORMED: int,
        consecutive_malformed: int,
        *,
        turn_control: ChatTurnControl,
        edit_decision_cb: EditDecisionCb | None,
        edit_record_cb: EditRecordCb | None,
        retrieval_delta_cb: RetrievalDeltaCb | None,
        on_pills_update: PillsUpdateCb | None = None,
        iteration_cb: Callable[[list[dict[str, object]]], None] | None = None,
        inbox_drain: Callable[[], list[InboxItem]] | None = None,
        report_guard: Callable[[], str | None] | None = None,
        terminal_guard: Callable[[], str | None] | None = None,
        status_tail: Callable[[], str] | None = None,
        final_hint: Callable[[], str] | None = None,
        report_check: ReportCheck | None = None,
        live_work: Callable[[], dict[str, object] | None] | None = None,
    ) -> ControllerOutcome:
        pending_salvage: list[str] = []
        # Set when preflight rejects generated code for a SYNTAX error, cleared as soon
        # as it is consumed. NIM's grammar silently corrupts escapes (`print("hello")`
        # -> `print("hello"}`): the JSON stays valid and the CODE breaks, so preflight is
        # the only place the corruption is observable. One unconstrained retry escapes
        # it; keeping it per-call means a GENUINE model syntax error (a missing colon, a
        # stray quote) costs one call of lost grammar rather than the whole session.
        retry_unconstrained = False
        unavailable_retries = 0

        def _on_thinking(chunk: str) -> None:
            # Stream the model's reasoning live so the chat thinking pane updates
            # during a model call (the FE maps tool_thinking_chunk). Raw token
            # chunks are live-only; durable thinking_log gets compact tool labels.
            self._broadcaster.broadcast(self._channel_id, {
                "type": "tool_thinking_chunk", "payload": {"chunk": chunk}})

        # Token counts are cumulative for the TURN, not per model call. A turn is
        # many calls, so per-call counts reset to near-zero on every sub-turn —
        # reading as progress going backwards, and doing it beside a turn-scoped
        # elapsed timer that never resets. `call_tokens` holds the in-flight
        # call's latest numbers so they can be folded into the turn total once it
        # finishes, whether it returned or raised.
        turn_tokens = {"reasoning": 0, "content": 0}
        call_tokens = {"reasoning": 0, "content": 0}

        def _fold_call_tokens_into_turn() -> None:
            turn_tokens["reasoning"] += call_tokens["reasoning"]
            turn_tokens["content"] += call_tokens["content"]
            call_tokens["reasoning"] = call_tokens["content"] = 0

        def _on_progress(
            reasoning_n: int,
            content_n: int,
            *,
            input_n: int | None = None,
            exact: bool = False,
        ) -> None:
            # Live token counts DURING a call. Its own channel, not the thinking pane:
            # a progress counter is not model reasoning (same rule as _on_retry and
            # edit_failed). Reasoning already streams via _on_thinking, but CONTENT
            # deltas are accumulated silently and only surface when the call returns —
            # so a long generation looks identical to a hang until this lands.
            #
            # `input_n` is the prompt size, known BEFORE the first delta. Without it the
            # counter reads zero for the whole prefill, which is most of the wall time on
            # a large prompt — a 372k-token prompt against a 262k-window model looked
            # exactly like a hang. `exact` separates the live chars/4 estimates from the
            # provider's own usage figure on the closing tick, so the UI can stop
            # presenting a moving guess as if it were settled.
            #
            # Keyword-only so a transport passing just the two positional counts keeps
            # working unchanged.
            call_tokens["reasoning"] = reasoning_n
            call_tokens["content"] = content_n
            self._broadcaster.broadcast(self._channel_id, {
                "type": "token_progress",
                "payload": {
                    "thinking": turn_tokens["reasoning"] + reasoning_n,
                    "output": turn_tokens["content"] + content_n,
                    "input": input_n,
                    "exact": exact,
                },
            })

        def _on_salvage(discarded_chars: int, discarded_text: str) -> None:
            # The transport parsed the FIRST complete object and dropped what followed
            # (a second action the model emitted in the same reply). Keeping that silent
            # desynchronises the model: its history would show only the first action's
            # result, so it can believe the second ran too. The transport reports the
            # fact; authoring the model-facing text belongs here.
            # Cause-neutral on purpose. Trailing content has two indistinguishable
            # causes: a genuine SECOND action (`{"type":…`), or surplus brackets /
            # an early close after the first. Naming one would be wrong about half
            # the time — the mistake this whole line of work exists to stop.
            pending_salvage.append(
                f"NOTE: {discarded_chars} character(s) after your first complete JSON "
                f"object were DISCARDED (starting: {discarded_text[:120]!r}). Only the "
                "first object was executed. Emit exactly ONE action per response, with "
                "balanced brackets and every \" inside a string escaped. If those "
                "characters were a second action you still intend, issue it now.")

        def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
            METER.record(USAGE_OWNER.get(), prompt=prompt_tokens, completion=completion_tokens)
            # self._observed_prompt is the provider's exact size for the LAST call,
            # pinned to the history length it measured. Compaction decides before the
            # next call is built, so this is necessarily one call behind —
            # `message_count` is what lets the compactor take the measured part exact
            # and estimate only what arrived since. Carried on `self` rather than a
            # plain closure var because run() seeds it from the caller's cross-turn
            # state and the caller reads it back after run() returns (see
            # partial_observed_prompt / self.__init__'s field comment).
            self._observed_prompt = ObservedPrompt(
                tokens=prompt_tokens, message_count=len(history))

        def _on_retry(attempt: int, max_attempts: int, reason: str, message: str) -> None:
            # Distinct channel from _on_thinking — a retry is not model reasoning
            # and must never be baked into the permanent thinking log (see design
            # spec docs/superpowers/specs/2026-07-14-retry-status-indicator-design.md).
            self._broadcaster.broadcast(self._channel_id, {
                "type": "retry_status",
                "payload": {
                    "attempt": attempt, "max_attempts": max_attempts,
                    "reason": reason, "message": message,
                },
            })

        # Real tool names only (never the schema's own top-level action types) —
        # feeds _answer_intent_divergence_correction. tool_defs is fixed for the
        # whole run, so this is computed once, not per iteration.
        tool_names = frozenset(str(d.get("name", "")) for d in tool_defs)

        # progress-action guardrail state, per turn (see _progress_repeat_correction /
        # _progress_dedup_correction). `last_was_progress` means "the last ACCEPTED
        # action was a note" — a rejected response deliberately leaves it alone, so two
        # notes separated only by junk are still caught as adjacent.
        #
        # Known boundary: it tracks ACCEPTED-ness, not whether real work happened, so the
        # five branches that accept an action and then `continue` without doing anything
        # clear it — answer/submit_changes blocked by the todo ledger, an edit with empty
        # patch_ops, a tool_call hitting DUPLICATE BLOCKED, and an edit whose apply raised
        # (PATCH FAILED). The last two are the loop's canonical stuck states, so they are
        # the likeliest to interleave with narration. Deliberately not closed: it would
        # mean threading a "did real work" flag through five unrelated pre-existing
        # branches, and _progress_dedup_correction already catches the repeated note.
        last_was_progress = False
        seen_notes: set[str] = set()

        for iteration in range(max_iters + 1):
            self._iteration = iteration
            # Close a pills segment marked since the last iteration (a gate breadcrumb
            # written mid-tool-call, a `progress` note) BEFORE anything this iteration
            # computes a call_index or persists a pill against it.
            self._apply_pills_boundary()
            if iteration_cb is not None and iteration > 0:
                # Persist after every iteration (spec §3.2): a stop or crash keeps everything
                # up to here, and a resume continues from it.
                iteration_cb(history)
            if inbox_drain is not None:
                for item in inbox_drain():
                    if item.kind in ("user", "team"):
                        # The user's own words (spec §5.3), or a team member's board delta
                        # (v2 §7.6) whose header is system-written and whose bodies are
                        # already framed — never framed again.
                        history.append({"role": "user", "content": item.text})
                        continue
                    author = item.author or item.source_id or "agent"
                    history.append({"role": "user", "content": "New message:\n"
                                    + frame(author, item.kind, item.text)})
            # Live "thinking" status so the chat UI isn't blank during the first model
            # call (the frontend maps chat_agent_thinking → the thinking pane). Only the
            # first iteration: subsequent activity is conveyed by tool pills + the live
            # work-bar timer, so re-emitting each turn would just spam duplicate entries.
            if iteration == 0:
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "chat_agent_thinking", "payload": {"message": "Thinking…"}})
            # Memory middleware: compact the live history in place before the model call
            # (no-op unless CRUCIBLE_MEMORY_ENABLED). history[:] keeps the same list object
            # partial_history() and downstream .append() calls reference.
            run_id = str(plan_context.get("run_id", "chat"))
            # Pass the current user message (goal) so recall has a query even on turn 1, when
            # the message lives in plan_context, not yet in history.
            _prep = await self._memory_harness.prepare_turn(
                history, run_id, query=str(plan_context.get("goal", "")),
                observed=self._observed_prompt,
                # A child's run never schedules consolidation (spec §5.2); the parent's
                # call is left exactly as it was.
                **({"consolidate": False} if self._agent is not None else {}))
            history[:] = _prep.history
            if _prep.compacted:
                # The pinned message_count no longer refers to this history.
                self._observed_prompt = None
            # Recalled long-term memories → the payload tail (KV-safe). Empty list omits it.
            plan_context["recalled_memories"] = _prep.recalled_memories
            # Phase 3: persist the recall trace next to the controller-turn artifacts (inspector).
            if _prep.recall_trace is not None:
                try:
                    from agentd.runtime.artifacts import chat_turn_artifacts_root
                    _tid = str(plan_context.get("artifact_thread_id") or "")
                    _turn = str(plan_context.get("artifact_turn_id") or "")
                    _ws = str(plan_context.get("workspace_path") or "")
                    if _tid and _turn:
                        _out = chat_turn_artifacts_root(_tid, _turn, _ws)
                        _out.mkdir(parents=True, exist_ok=True)
                        (_out / f"memory-recall-{iteration:02d}.json").write_text(
                            _prep.recall_trace.model_dump_json(indent=2), encoding="utf-8")
                except Exception:  # noqa: BLE001 — best-effort
                    logger.warning("[memory] recall-trace dump failed")
            if _prep.compacted:
                # Observability: surface the compaction so the chat UI can show it fired.
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "memory_compacted",
                    "payload": {
                        "evicted": _prep.evicted_count,
                        "anchor_version": _prep.anchor_version,
                    },
                })
            # Re-surface the live todo ledger into the payload tail every iteration so the
            # model re-reads its own contract (the detail that makes discretion stick). Empty
            # string when no list exists -> build_controller_step_payload omits it.
            plan_context["todo_status"] = self._ledger.render()
            if status_tail is not None:
                # Rebuilt from the database every iteration (spec v2 §7.6) — cheap, and the
                # only copy of the phase state that survives a long activation or compaction.
                plan_context["team_status"] = status_tail()
            if live_work is not None:
                # The thread's teams and agents still at work — read every iteration, since
                # a team moves phase and agents finish while this turn runs.
                live = live_work()
                if live:
                    plan_context["live_work"] = live
                else:
                    plan_context.pop("live_work", None)
            # Spec v2 §3.11: a forced final (deadline or budget) tells the model what its
            # phase needs before it reports.
            plan_context["forced_final"] = self._force_final
            if final_hint is not None and (iteration >= max_iters or self._force_final):
                plan_context["team_final_hint"] = final_hint()
            # Re-inject activated skill bodies into the tail every iteration (compaction-
            # resilient); empty list -> build_controller_step_payload omits it.
            plan_context["active_skills"] = [
                {"name": n, "body": b} for n, b in self._active_skills.items()
            ]
            # active_entry (C1b): first action in ACTIVE, nothing started yet (no list,
            # no edit applied). The payload builder swaps the clean entry hint
            # (write_todos-as-tool_call) for the mid-turn reconcile hint once this clears —
            # which it does the moment a list exists OR an edit lands. NO iteration clause
            # — this is deliberate: it must persist through an empty-edit fumble (the model
            # emits an edit with empty patch_ops, nothing lands) so the NEXT iteration still
            # sees "nothing started yet" rather than falling through to the mid-turn
            # "reflect on your last edit's result" text written for a LANDED edit. This is a
            # previously-fixed thrash bug — do not add an iteration gate here.
            delegated = self._delegation()
            if delegated is not None:
                plan_context["delegated"] = delegated
            else:
                plan_context.pop("delegated", None)
            plan_context["active_entry"] = (
                self._sm.phase == "ACTIVE" and not self._ledger.items
                and not self._edit_applied and not plan_context.get("edit_is_resume")
                and delegated is None)
            # skill_check_due (C1b): the first model call of THIS run only (iteration is
            # the for-loop counter above, fresh every run() — unlike `history`, which
            # seeds from the whole thread's replayed conversation and is non-empty for
            # every message after the thread's first ever). Unlike active_entry, this IS
            # strictly one-shot — skill triage is genuinely redundant to re-run every
            # iteration once it's been done or missed.
            plan_context["skill_check_due"] = self._sm.phase == "ACTIVE" and iteration == 0
            # plan_entry (PLAN): unchanged semantics from the old DECIDE branch, just
            # renamed to the PLAN phase value — the first model call of THIS run only.
            plan_context["plan_entry"] = self._sm.phase == "PLAN" and iteration == 0
            # THIS turn's step count. The payload builder otherwise infers it from
            # len(history), which carries the whole thread and so never resets per turn.
            plan_context["iteration"] = iteration
            try:
                step_fn = self._reasoning.create_controller_step
                # New keywords only for engines that declare them — the same ask-the-callee
                # rule as engine._accepts, so fixed-signature fakes keep working.
                seam_kwargs: dict[str, object] = {}
                if accepts_kwarg(step_fn, "allowed_types"):
                    seam_kwargs["allowed_types"] = self._allowed_action_types()
                if accepts_kwarg(step_fn, "render_ctx"):
                    seam_kwargs["render_ctx"] = self._render_ctx
                if self._agent is not None and accepts_kwarg(step_fn, "persona"):
                    seam_kwargs["persona"] = self._agent.persona
                resp = await step_fn(
                    plan_context=plan_context, history=history,
                    tool_definitions=tool_defs, phase=self._sm.phase,
                    on_thinking=_on_thinking, on_retry=_on_retry,
                    on_progress=_on_progress, on_salvage=_on_salvage,
                    on_usage=_on_usage,
                    unconstrained=retry_unconstrained,
                    **seam_kwargs,
                )
                retry_unconstrained = False   # one call only, always
                unavailable_retries = 0
            except Exception as exc:
                # A plan usage limit, an ineligible account or a dead sign-in: neither a
                # retry nor a correction message can fix it, so the turn ends here.
                stopped = find_access_stop(exc)
                if stopped is exc:
                    raise
                if stopped is not None:
                    raise stopped from exc
                if is_provider_unavailable(exc):
                    if unavailable_retries < len(PROVIDER_RETRY_BACKOFFS_SEC):
                        delay = PROVIDER_RETRY_BACKOFFS_SEC[unavailable_retries]
                        unavailable_retries += 1
                        logger.warning("[controller] provider unavailable (%d/%d), retrying "
                                       "in %.0fs: %s", unavailable_retries,
                                       len(PROVIDER_RETRY_BACKOFFS_SEC), delay, exc)
                        _on_retry(unavailable_retries, len(PROVIDER_RETRY_BACKOFFS_SEC),
                                  "provider_unavailable",
                                  f"⚠️ Provider unavailable — retrying in {delay:.0f}s…")
                        # Not malformed, and nothing is appended to history: the model did
                        # nothing wrong, so a correction message would only mislead it.
                        await asyncio.sleep(delay)
                        continue
                    raise ProviderUnavailable(str(exc)) from exc
                # A raised exception here (empty/unparseable model output, a transport
                # hiccup — e.g. a cloud model exhausting its output budget on <think>)
                # is just another flavor of "the model gave me nothing usable" — the
                # SAME thing consecutive_malformed already exists to handle for a
                # parsed-but-invalid response below. Route it through that one shared
                # counter/correction path instead of a second bespoke retry mechanism.
                logger.warning("[controller] create_controller_step raised: %s", exc)
                consecutive_malformed += 1
                if consecutive_malformed > _MAX_MALFORMED:
                    raise ControllerLoopExhausted(
                        f"Controller failed {consecutive_malformed} consecutive times "
                        f"— last error: {exc}") from exc
                # A malformed/failed response can recur for several iterations before
                # recovering or exhausting — without this the UI shows nothing between
                # "Working…" and either the next real action or the eventual failure,
                # indistinguishable from actually being stuck.
                _on_retry(
                    consecutive_malformed, _MAX_MALFORMED, "malformed_response",
                    f"⚠️ Response failed ({consecutive_malformed}/{_MAX_MALFORMED}): "
                    f"{cap_event_output(str(exc), 200)} — retrying…",
                )
                history.append({"role": "assistant", "content": "{}"})
                history.append({
                    "role": "tool_result", "tool": "",
                    "content": (
                        f"Your previous response failed: {cap_event_output(str(exc), 300)} "
                        + _parse_failure_guidance(str(exc))
                    ),
                })
                continue
            finally:
                # Runs on the `continue` paths too, so a call that raised still
                # contributes the tokens it burned before failing — otherwise a
                # retry would silently rewind the turn's counter.
                _fold_call_tokens_into_turn()

            atype = str(resp.get("type", ""))
            logger.info("[controller] iter=%d phase=%s action=%s", iteration, self._sm.phase, atype)
            # Drain any salvage notice BEFORE dispatching: the model must see that its
            # trailing action was dropped, on the same turn it happened.
            while pending_salvage:
                note = pending_salvage.pop(0)
                logger.info("[controller] %s", note.split(" (starting")[0])
                history.append({"role": "tool_result", "tool": "", "content": note})
            # Reject BEFORE dispatching: wrong action type for the phase, a propose_mode with
            # invalid mode vocabulary, OR a well-typed action with an empty REQUIRED field (the
            # flat schema permits {"type":"answer"} / empty tool_call — see
            # _empty_action_correction). Each is corrected + retried, bounded by _MAX_MALFORMED.
            correction = (
                (_tool_name_as_type_correction(atype, tool_names)
                 or _unavailable_type_correction(
                     atype, self._allowed_action_types(), self._render_ctx)
                 or malformed_correction(self._render_ctx, self._allowed_action_types()))
                if atype not in self._allowed_action_types()
                else _propose_mode_correction(resp, self._allowed_modes_for_current_phase()) if atype == "propose_mode"
                else _reserved_tool_name_correction(resp, atype, self._render_ctx)
                or _decide_state_change_correction(resp, self._sm.phase)
                or _permission_correction(resp, atype, self._render_ctx)
                or _empty_action_correction(resp, atype)
                or _answer_intent_divergence_correction(resp, atype, tool_names)
                # After _empty_action_correction on purpose: a blank note must be
                # reported as EMPTY, not misdiagnosed as a duplicate of "".
                or _progress_repeat_correction(resp, atype, last_was_progress, self._render_ctx)
                or _progress_dedup_correction(resp, atype, seen_notes, self._render_ctx)
            )
            if correction is not None:
                if atype == "propose_mode":
                    logger.warning(
                        "[controller] propose_mode REJECTED: recommended=%r options=%r",
                        resp.get("recommended"), resp.get("options"))
                else:
                    logger.info(
                        "[controller] %s REJECTED — tool=%r args=%r (type=%s) correction=%s",
                        atype,
                        resp.get("tool"), resp.get("args"), type(resp.get("args")).__name__,
                        correction[:120] if correction else None,
                    )
                consecutive_malformed += 1
                if consecutive_malformed > _MAX_MALFORMED:
                    raise ControllerLoopExhausted(
                        f"Controller returned {consecutive_malformed} consecutive malformed "
                        f"responses (last type={atype!r})")
                if consecutive_malformed >= 2:
                    # A rejection alone isn't landing — the model reads the correction but
                    # keeps retrying (observed live: 5 identical `run_command` attempts in
                    # DECIDE phase, each rejected the same way, exhausting the budget without
                    # ever adapting to `propose_mode`). Make the shrinking runway explicit
                    # rather than relying on the correction text alone.
                    remaining = _MAX_MALFORMED - consecutive_malformed
                    correction = (
                        f"⚠ Retry {consecutive_malformed}/{_MAX_MALFORMED} — "
                        f"{remaining} attempt(s) left before this turn fails outright. "
                        + correction
                    )
                _on_retry(
                    consecutive_malformed, _MAX_MALFORMED, "malformed_response",
                    f"⚠️ Invalid response ({consecutive_malformed}/{_MAX_MALFORMED}): "
                    f"{cap_event_output(correction, 200)} — retrying…",
                )
                # A REJECTED `progress` still needs the SAME cap the accept path applies
                # (see the atype == "progress" branch below) — _progress_repeat_correction
                # and _progress_dedup_correction both reject actual `progress` responses,
                # and without this the raw (uncapped) note rides every subsequent prompt
                # until _MAX_MALFORMED, exactly the context bloat the cap exists to bound
                # (final whole-branch review, finding 2). Scoped to `progress` rather than
                # a blanket fix here: this file's existing idiom for a large-field rejection
                # is a targeted strip/substitute at the field whose bloat risk is understood
                # (e.g. the empty-edit and PATCH-FAILED branches below both drop patch_ops
                # from the persisted intent) — a generic cap at this shared site would need
                # to know which field matters per type anyway, and 'progress' is the only
                # type with an established cap to reuse here.
                persisted = (
                    {**resp, "note": _normalize_progress_note(resp)}
                    if atype == "progress" else resp
                )
                history.append(assistant_turn(persisted))
                history.append({"role": "tool_result", "tool": "", "content": correction})
                continue
            # Captured BEFORE the reset so the progress branch can restore it — a note
            # is not evidence of progress, so it must not zero a malformed streak.
            prev_malformed = consecutive_malformed
            consecutive_malformed = 0
            # Any accepted action clears the progress-adjacency flag; the progress branch
            # below sets it back to True. (Placed here so it only runs on an ACCEPTED
            # action — a corrected response `continue`s above and never reaches this.)
            last_was_progress = False
            if atype == "answer":
                # C2: answer is reachable from ACTIVE (unlike the old EDIT phase, which
                # only had submit_changes as a terminal). Block it ONLY when THIS turn
                # itself applied an edit and the ledger still has open items — the same
                # class of guarantee submit_changes already enforces, but scoped tighter:
                # a stale unrelated ledger must never block ordinary Q&A that never
                # touched it (see the negative-control test in
                # test_controller_loop_answer_ledger_gate.py).
                if self._edit_applied and self._ledger.pending():
                    still_open = self._ledger.pending()
                    titles = ", ".join(i.title for i in still_open)
                    history.append(assistant_turn(resp))
                    history.append({
                        "role": "tool_result", "tool": "",
                        "content": (
                            f"'answer' is BLOCKED — {len(still_open)} todo item(s) are still "
                            f"pending/in_progress after edits you made this turn: {titles}. "
                            "Reconcile the list first (mark items done with evidence, or "
                            "'blocked' with a reason), or emit 'submit_changes' once it's "
                            "clear. If nothing here needs finishing, emit 'answer' again "
                            "unchanged."
                        ),
                    })
                    continue
                if terminal_guard is not None and iteration < max_iters:
                    redirect = terminal_guard()
                    if redirect is not None:
                        # A redirect, not malformed (same contract as the open-todo block).
                        history.append(assistant_turn(resp))
                        history.append({"role": "tool_result", "tool": "", "content": redirect})
                        continue
                history.append(assistant_turn(resp))
                return ControllerOutcome(
                    kind="answer", text=str(resp.get("answer", "")), history=history)
            if atype == "progress":
                # A progress note is not evidence of progress: restore the malformed
                # counter so a stuck model can't launder its malformed streak through
                # interleaved notes to evade _MAX_MALFORMED.
                consecutive_malformed = prev_malformed
                note = _normalize_progress_note(resp)
                seen_notes.add(note)
                last_was_progress = True
                # This note is a pills-segment boundary: everything from here on (live
                # call_index, the durable on_pills_update payload, and the final outcome's
                # own tool_events/thinking_log if the turn ends before another tool_call)
                # is scoped to "since THIS note", not the whole turn — mirroring the live
                # webview's sealStreaming, which resets its streaming bubble's toolEvents
                # to [] right after sealing one into a message. Same mechanism every other
                # mid-turn durable message uses (see mark_pills_boundary); applied at the
                # next iteration top, which is before the next pill either way.
                self.mark_pills_boundary()
                # Live event (durable half is the cb below). Mirrors tool_call's
                # broadcast-live / persist-separately split.
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "chat_progress", "payload": {"note": note}})
                if self._progress_note_cb is not None:
                    try:
                        await self._progress_note_cb(note)
                    except Exception:  # noqa: BLE001 — a narration write must never kill a turn
                        logger.warning("[controller] progress_note_cb failed", exc_info=True)
                # Persist the NORMALIZED note, not `resp`'s raw one: assistant_turn strips
                # only 'thought', so passing resp verbatim would ride the uncapped note
                # into every subsequent iteration's prompt — the exact context bloat
                # _PROGRESS_NOTE_MAX_CHARS exists to prevent. Same substitute-before-
                # persisting shape the edit branch uses for patch_ops.
                history.append(assistant_turn({**resp, "note": note}))
                history.append({
                    "role": "tool_result", "tool": "",
                    "content": (
                        f'Progress note posted to the user: "{note}". This did NOT end the '
                        "turn — now take the actual next action."
                    ),
                })
                continue
            if atype == "tool_call":
                if iteration >= max_iters:
                    return ControllerOutcome(
                        kind="answer", text="(step budget exhausted)", history=history)
                tool = str(resp.get("tool", ""))
                raw_args = resp.get("args")
                args: dict[str, object] = raw_args if isinstance(raw_args, dict) else {}
                key = dedup_key(tool, args)
                if key in seen:
                    history.append({"role": "assistant", "content": "{}"})
                    history.append({
                        "role": "tool_result", "tool": tool,
                        "content": f"DUPLICATE BLOCKED (iter {seen[key]}); do differently.",
                    })
                    continue
                seen[key] = iteration + 1
                # Observability: log every tool call/result (ToolLoop does the same).
                # Without this, controller turns are invisible in logs — you can't tell
                # whether a turn explored or emitted straight from seed_history.
                logger.info("[controller] tool_call phase=%s iter=%d tool=%s args=%s",
                            self._sm.phase, iteration, tool, str(args)[:200])
                # Live tool pill: tool_call before execute, tool_result after. The
                # frontend pairs these by source ("execution") into a pill with thought.
                # call_index = the position this call will occupy WITHIN THE CURRENT PILLS
                # SEGMENT (it's appended below), which equals the persisted pill id
                # (trace_to_tool_events enumerates the segment-sliced list below, not the
                # whole-turn one — see self._pill_segment_start). The FE uses it as the
                # pill id so a switch-back resume dedups replayed pills against the loaded
                # in-flight message; relative-to-segment keeps it valid across a `progress`
                # note, which starts a fresh in-flight message (finding 1).
                call_index = len(self._calls) - self._pill_segment_start
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "tool_call",
                    "payload": {"tool": tool, "thought": str(resp.get("thought", "")),
                                "args": args, "call_index": call_index}})
                out = await self._registry.execute(tool, args)
                # The model reconciled the ledger — clear the post-edit checkpoint marker so
                # the next turn's instruction stops naming the (now-answered) edit/item (Q1).
                if tool == "write_todos":
                    plan_context.pop("pending_reconcile_files", None)
                    plan_context.pop("reconcile_item", None)
                if not out.is_error:
                    self._note_delegation(tool, args)
                logger.info("[controller] tool_result tool=%s is_error=%s chars=%d",
                            tool, out.is_error, len(out.output or ""))
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "tool_result",
                    "payload": {"output": out.output, "is_error": out.is_error,
                                "call_index": call_index}})
                # Record into the turn trace → persisted as durable pills (reload).
                call_id = f"c{iteration}"
                self._calls.append(ToolCall(
                    call_id=call_id, tool_name=tool, arguments=args,
                    thought=str(resp.get("thought", "")) or None))
                self._results.append(ToolResult(
                    call_id=call_id, tool_name=tool, output=out.output, is_error=out.is_error))
                # Durable thinking entry (compact label, not the raw token stream).
                thought = str(resp.get("thought", ""))
                path = str(args.get("path", "")) if isinstance(args, dict) else ""
                label = f" {path.split('/')[-1]}" if path else ""
                self._thinking.append(
                    f"{tool}{label} — {thought[:200]}" if thought else f"{tool}{label}")
                # Durably persist the partial pills now (finding 5): a thread switch /
                # reopen before turn end reconstructs them from the transcript instead of
                # the lossy replay buffer. Best-effort — a persist failure never breaks
                # the turn.
                if on_pills_update is not None:
                    try:
                        # Sliced to the CURRENT segment (since the last `progress` note, or
                        # turn start if none yet) — see self._pill_segment_start. A no-note
                        # turn has segment_start==0 for its whole life, so this is a no-op
                        # slice ([0:] == the full list) and byte-identical to pre-fix.
                        pills = trace_to_tool_events(
                            AgentToolTrace(
                                step_id="chat",
                                calls=self._calls[self._pill_segment_start:],
                                results=self._results[self._pill_segment_start:]),
                            "execution")
                        await on_pills_update(
                            pills, list(self._thinking[self._thinking_segment_start:]))
                    except Exception:
                        logger.debug("[controller] inflight pill persist failed", exc_info=True)
                history.append(assistant_turn(resp))
                history.append({"role": "tool_result", "tool": tool, "content": out.output})
                if out.workspace_changes:
                    # Another agent promoted files on this agent's behalf (a dispatch,
                    # spec §6.4): the same bookkeeping as this loop's own accepted edit —
                    # repeats are no longer duplicates, the turn has edited, and the tail
                    # gets a compact refresh note (never a seed rewrite).
                    seen.clear()
                    self._edit_applied = True
                    if retrieval_delta_cb is not None:
                        delta = await retrieval_delta_cb(list(out.workspace_changes))
                        if delta:
                            history.append({
                                "role": "tool_result", "tool": "retrieval_refresh",
                                "content": delta})
                continue
            if atype == "clarify":
                history.append(assistant_turn(resp))
                raw_opts = resp.get("options")
                options = [str(o) for o in raw_opts] if isinstance(raw_opts, list) else []
                question = str(resp.get("question", ""))
                return ControllerOutcome(
                    kind="clarify", text=question, history=history,
                    payload={"question": question, "options": options})
            if atype == "propose_mode":
                history.append(assistant_turn(resp))
                return ControllerOutcome(kind="propose_mode", payload={
                    "plan_sketch": resp.get("plan_sketch", ""),
                    "recommended": _normalized_recommended(resp),
                    "reason": resp.get("reason", ""),
                    "options": resp.get("options", []),
                }, history=history)
            if atype == "edit":
                # Lazily construct on first use (C1) — ACTIVE is the default phase, so
                # a plain turn that never edits never pays for a session/shadow at all.
                if self._edit is None:
                    if self._edit_session_factory is None:
                        raise RuntimeError("edit requires an orchestrator (no edit_session_factory)")
                    self._edit = self._edit_session_factory()
                raw_ops = resp.get("patch_ops")
                ops: list[dict[str, object]] = raw_ops if isinstance(raw_ops, list) else []
                logger.info("[controller] edit phase=%s ops=%d files=%s",
                            self._sm.phase, len(ops),
                            [op.get("file") for op in ops if isinstance(op, dict)])
                if not ops:
                    # Empty 'edit' = the live write_todos mis-route: the model's thought wants the
                    # todo list ("I'll use write_todos first") but it picked type='edit' (the only
                    # "do something" action it associates with EDIT) and shipped no ops. The old
                    # apply([]) error ("emit at least one op or submit_changes") pushed it to retry
                    # the SAME empty edit — observed thrashing 4+ turns. Redirect to the RIGHT action
                    # type with exact syntax. A redirect, NOT a malformed action (don't touch
                    # consecutive_malformed) — only max_iters bounds it.
                    logger.info("[controller] empty edit (ops=0) — redirecting (write_todos/edit/submit)")
                    history.append(assistant_turn(
                        {k: v for k, v in resp.items() if k != "patch_ops"}))
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": _empty_edit_redirect(self._render_ctx)})
                    continue
                try:
                    diff = await self._edit.apply(ops)
                except Exception as exc:
                    # A malformed op (code-in-'file'), bad search string, policy violation, or
                    # ambiguous selector raises. Feed it back so the agent re-emits instead of
                    # crashing the turn — mirrors ToolLoop._apply_patch_inline. CRUCIAL: do NOT
                    # echo the malformed patch_ops into history; a weak model copies its own bad
                    # op into a repetition attractor (see planning/loop.py thought-strip). Persist
                    # only the failed *intent*, and let the exception message carry the fix.
                    #
                    # Observability: a failed edit produces NO diff card (edit_record_cb only
                    # fires on success), so without this it is invisible — the UI shows a silent
                    # wait while the model thrashes. Log it AND surface a LIVE-ONLY thinking
                    # line ("✗ edit failed: <reason>") so the failure is legible in agentd.log
                    # and the chat thinking pane while the turn runs.
                    #
                    # Its OWN event type, and NOT appended to self._thinking. A
                    # preflight/engine error is not model reasoning: on the thinking
                    # channel the webview renders it as a numbered reasoning step,
                    # indistinguishable from the model's own thought. Same rule
                    # _on_retry follows with retry_status, for the same reason.
                    #
                    # This does NOT withhold the error from the model. These channels are
                    # UI-only — the model's context comes from `history` (persisted as
                    # ChatThread.controller_conversation_history), and the PATCH FAILED
                    # tool_result appended below is how it reconciles. One is what the
                    # user sees, one is what the model reads.
                    reason_line = str(exc).splitlines()[0][:200] if str(exc) else "unknown error"
                    logger.info("[controller] edit FAILED phase=%s ops=%d: %s",
                                self._sm.phase, len(ops), reason_line)
                    # A syntax rejection is the ONLY observable signal of grammar
                    # corruption (see retry_unconstrained). Genuine model syntax errors
                    # trip it too — accepted, because the cost is one unconstrained call.
                    if "syntax error" in reason_line.lower():
                        retry_unconstrained = True
                        logger.info("[controller] syntax rejection — next model call "
                                    "will skip the grammar (one call only)")
                    self._broadcaster.broadcast(self._channel_id, {
                        "type": "edit_failed",
                        "payload": {"reason": reason_line, "ops": len(ops)}})
                    intent = {k: v for k, v in resp.items() if k != "patch_ops"}
                    history.append(assistant_turn(intent))
                    guidance = _edit_failure_guidance(exc)
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"PATCH FAILED: {exc}"
                                   + (f" {guidance}" if guidance else "")})
                    continue
                # Auto-accept (instant promote) OR hold for a per-edit review decision.
                # The decision cb holds the SSE stream open + renders the live diff via
                # the /live EditGate (review mode only). The loop does NOT broadcast
                # diff_ready — edit_record_cb is the single transcript writer (durable
                # diff_card + the auto-accept live render), so nothing dangles on reload.
                #
                # Re-read the preference HERE, per edit — never a value captured at turn
                # start. The composer checkbox is live (POST /chat/threads/{id}/review-pref
                # mutates this same object), so one turn can legitimately gate its first
                # edit and auto-accept its third.
                # A protected-path edit always asks, whatever the preference (spec §3.9).
                was_gated = edit_decision_cb is not None and (
                    not turn_control.auto_accept_edits or self._edit.requires_review)
                reason = ""
                if was_gated:
                    decision = await edit_decision_cb(diff)
                    accepted = decision.get("decision") == "accept"
                    reason = str(decision.get("reason", ""))
                else:
                    accepted = True
                stale: StaleWriteError | None = None
                refused: ProtectedPathError | None = None
                if accepted:
                    try:
                        await self._edit.accept()
                    # Narrow on purpose and FIRST: StaleWriteError is a RuntimeError, so
                    # anything broader would swallow it (rev 11 §7.4). Check 2 refused the
                    # promote — a sibling changed a file while this edit was in flight or
                    # held at the gate; accept() already restored the shadow from real.
                    except StaleWriteError as exc:
                        stale = exc
                    # A symlink made after apply redirected the promote into a protected
                    # path (spec §3.9); accept() already restored the shadow.
                    except ProtectedPathError as exc:
                        refused = exc
                else:
                    await self._edit.reject()  # restore shadow from real (shadow==real)
                if refused is not None:
                    if edit_record_cb is not None:
                        await edit_record_cb(diff, "reject", str(refused), was_gated)
                    history.append(assistant_turn(
                        {k: v for k, v in resp.items() if k != "patch_ops"}))
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"PATCH FAILED: {refused} {_edit_failure_guidance(refused)}"})
                    continue
                if stale is not None:
                    if edit_record_cb is not None:
                        await edit_record_cb(
                            diff, "stale",
                            f"{stale.path} changed since it was read (by {stale.writer_label})",
                            was_gated)
                    history.append(assistant_turn(
                        {k: v for k, v in resp.items() if k != "patch_ops"}))
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"PATCH FAILED: {stale} {_edit_failure_guidance(stale)}"})
                    continue
                if edit_record_cb is not None:
                    await edit_record_cb(
                        diff, "accept" if accepted else "reject", reason, was_gated)
                history.append(assistant_turn(resp))
                if accepted:
                    touched = [d.path for d in diff]
                    # A real edit landed → out of the active-entry window (clears active_entry).
                    self._edit_applied = True
                    # Log the apply outcome (the diff card carries the UI; agentd.log had no
                    # record of a successful apply — only the pre-apply ops line at L302).
                    logger.info("[controller] edit applied phase=%s files=%s",
                                self._sm.phase, touched)
                    # A plan document this edit just wrote may itself name a
                    # REQUIRED SUB-SKILL for its own execution — force-load it now
                    # rather than rely on the model noticing its own plan text later
                    # in this same turn (see _maybe_force_required_subskill).
                    await self._maybe_force_required_subskill(ops, history)
                    # The workspace state just changed — a tool_call that duplicates an
                    # EARLIER (pre-edit) call is no longer a mindless repeat, it may now be
                    # the objectively correct next step (e.g. retrying the same `uv add`
                    # after fixing the pyproject.toml it failed on). Without this the model
                    # can get permanently wedged: DUPLICATE BLOCKED forever with no way to
                    # signal "I need to redo this now that state changed" (found live:
                    # 35+ iterations / ~30min stuck retrying an identical run_command after
                    # a state-changing edit). Mirrors verify_phase_sm's emit_patch dedup,
                    # which also clears on every transition.
                    seen.clear()
                    # Q1 reconcile checkpoint: when a todo list is ACTIVE (something still
                    # open), flag the just-edited files + the active item so the NEXT turn's
                    # instruction leads with a pointed "is THIS item done?" question. This is
                    # NOT a hard gate — an edit may only PARTIALLY complete an item, so forcing
                    # a write_todos would pressure a false 'done'. The model answers the
                    # checkpoint by marking done OR continuing the same item; the marker clears
                    # the moment write_todos runs (see the tool_call branch). Soft enforcement:
                    # if this still slips on a weak model, the next escalation is a gate that
                    # forces a write_todos call but accepts 'in_progress' as a valid answer.
                    if self._ledger.pending():
                        plan_context["pending_reconcile_files"] = touched
                        active = self._ledger.active_item()
                        if active is not None:
                            plan_context["reconcile_item"] = {
                                "title": active.title, "status": active.status}
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"applied+promoted: {touched}"})
                    # Append-only retrieval delta (spec §6): never rewrites the seed,
                    # only adds a compact refresh note to the cached tail.
                    if retrieval_delta_cb is not None:
                        delta = await retrieval_delta_cb(touched)
                        if delta:
                            history.append({
                                "role": "tool_result", "tool": "retrieval_refresh",
                                "content": delta})
                else:
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"REJECTED by user: {reason}. Revise and re-emit."})
                continue
            if atype == "report":
                # A sub-agent's only terminal and its WHOLE deliverable (spec §5.1, D8): the
                # summary is returned verbatim, never truncated.
                still_open = self._ledger.pending()
                final = iteration >= max_iters or self._force_final
                if report_guard is not None and not final:
                    blocked = report_guard()
                    if blocked is not None:
                        # A redirect, not malformed (same contract as the open-todo block).
                        history.append(assistant_turn(resp))
                        history.append({"role": "tool_result", "tool": "", "content": blocked})
                        continue
                if still_open and not final:
                    # Same contract as submit_changes: a redirect, NOT a malformed action,
                    # so it does not touch consecutive_malformed — only max_iters bounds it.
                    titles = ", ".join(i.title for i in still_open)
                    history.append(assistant_turn(resp))
                    history.append({
                        "role": "tool_result", "tool": "",
                        "content": (
                            f"report BLOCKED — {len(still_open)} todo item(s) still open: "
                            f"{titles}. Continue with the next item, then call write_todos to "
                            "mark it 'done' (cite evidence in 'note'). If one is genuinely "
                            "stuck, mark it 'blocked' (with the unblock reason) or 'cancelled' "
                            "(with why). Report once nothing is pending."),
                    })
                    continue
                if report_check is not None:
                    verdict = report_check(resp, final)
                    if verdict.message is not None and verdict.malformed:
                        # A byte-identical resubmission is malformed (spec v2 §8.3) — the
                        # counter was reset for this accepted action, so continue the streak.
                        consecutive_malformed = prev_malformed + 1
                        if consecutive_malformed > _MAX_MALFORMED:
                            # Exhausted by report validation alone: accept with the invalid
                            # entries dropped, so the member stays in the quorum.
                            verdict = report_check(resp, True)
                    if verdict.message is not None:
                        history.append(assistant_turn(resp))
                        history.append({"role": "tool_result", "tool": "",
                                        "content": verdict.message})
                        continue
                history.append(assistant_turn(resp))
                summary = str(resp.get("summary", "")).strip()
                if still_open:
                    # Final iteration: the budget is spent, so the ledger block is bypassed
                    # and the dispatcher is told exactly what is left (spec §6.5).
                    summary += "\n\nUnfinished:\n" + "\n".join(
                        f"- {i.title} ({i.status})" for i in still_open)
                chosen = str(resp.get("status") or "completed")
                status = ("partial" if final
                          else chosen if chosen in self._report_statuses else "completed")
                return ControllerOutcome(
                    kind="report", text=summary, history=history,
                    payload={"status": status})
            if atype == "submit_changes":
                # Hard gate: a non-empty ledger is a contract. Block submit while items are
                # pending/in_progress (NOT blocked/cancelled/done — those never deadlock) and
                # redirect to the next item. This is a legitimate redirect, NOT a malformed
                # action, so it does NOT touch consecutive_malformed — only max_iters bounds it.
                still_open = self._ledger.pending()
                if still_open:
                    titles = ", ".join(i.title for i in still_open)
                    history.append(assistant_turn(resp))
                    history.append({
                        "role": "tool_result", "tool": "",
                        "content": (
                            f"submit_changes BLOCKED — {len(still_open)} todo item(s) still "
                            f"open: {titles}. Continue with the next item (one edit at a time), "
                            "then call write_todos to mark it 'done' (cite evidence in 'note'). "
                            "If one is genuinely stuck, mark it 'blocked' (with the unblock "
                            "reason) or 'cancelled' (with why). Do NOT submit until nothing is "
                            "pending."),
                    })
                    continue
                if terminal_guard is not None and iteration < max_iters:
                    redirect = terminal_guard()
                    if redirect is not None:
                        # A redirect, not malformed (same contract as the open-todo block).
                        history.append(assistant_turn(resp))
                        history.append({"role": "tool_result", "tool": "", "content": redirect})
                        continue
                # The shadow is closed by run()'s finally on return (no double-close).
                history.append(assistant_turn(resp))
                # Deterministic fallback for an empty summary — unlike answer/clarify we do NOT
                # retry (the edits are already promoted; retrying-to-exhaustion would convert a
                # done turn into a failure). A non-empty summary keeps the closing chat message
                # from collapsing to nothing (the "no closing message" gap).
                summary = str(resp.get("summary", "")).strip() or "Changes submitted."
                return ControllerOutcome(
                    kind="submit_changes", text=summary, history=history)
            raise NotImplementedError(atype)
        return ControllerOutcome(kind="answer", text="(loop ended)", history=history)
