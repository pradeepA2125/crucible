"""ControllerLoop — the agentic chat-turn ReAct loop (mirrors PlanningLoop).

Reads always hit the real workspace (no shadow-read flip). Terminal actions own
their own teardown. E1 implements explore (tool_call) + answer; clarify/propose_mode/
edit/submit_changes are added in E2/E3.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.tool_events import trace_to_tool_events
from agentd.domain.models import AgentToolTrace, ToolCall, ToolResult
from agentd.memory.harness import NO_OP_HARNESS, MemoryHarness
from agentd.orchestrator.broadcaster import cap_event_output
from agentd.reasoning.react_common import MALFORMED_CORRECTION, assistant_turn, dedup_key
from agentd.skills.config import skills_body_max_chars

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.chat.edit_session import TurnEditSession
    from agentd.domain.models import DiffEntry
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.reasoning.contracts import ReasoningEngine
    from agentd.tools.sources import AggregatingToolRegistry

    EditDecisionCb = Callable[[list[DiffEntry]], Awaitable[dict[str, object]]]
    # Persist+render an edit's resolution (diff, decision: "accept"|"reject", reason).
    # The controller owns the durable diff_card record + transcript broadcast; the
    # loop no longer broadcasts diff_ready itself (Class-A: the record cb is the one
    # writer, so every edit survives a reload — smoke-found gap #2/#5).
    EditRecordCb = Callable[[list[DiffEntry], str, str], Awaitable[None]]
    # Given the files an accepted edit touched, return a compact retrieval-refresh
    # note (pointers only, no bodies) to append to history — or None.
    RetrievalDeltaCb = Callable[[list[str]], Awaitable[str | None]]
    # Persist the in-flight turn's pills incrementally (tool_events, thinking_log) so a
    # thread switch / panel reopen mid-turn reconstructs them durably (finding 5).
    PillsUpdateCb = Callable[[list[dict], list[str]], Awaitable[None]]


logger = logging.getLogger(__name__)


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
    return None


# The top-level response `type`s (see CONTROLLER_RESPONSE_SCHEMA) — never real tool
# names. A model that just correctly used {"type":"tool_call","tool":"write_todos",...}
# can generalize the same shape onto these (most often 'edit'), since nothing in the
# tool_call schema constrains 'tool' to the registry. The registry's generic "unknown
# tool" error gives it no signal to self-correct from, so it can grind on the identical
# illegal call indefinitely (found live: an hour+ of retries, each a full regeneration).
_RESERVED_ACTION_TOOL_NAMES = frozenset(
    {"answer", "clarify", "propose_mode", "edit", "submit_changes"})


def _reserved_tool_name_correction(resp: dict[str, object], atype: str) -> str | None:
    """Reject a tool_call whose 'tool' is actually a top-level response type name."""
    if atype != "tool_call":
        return None
    tool = str(resp.get("tool", ""))
    if tool not in _RESERVED_ACTION_TOOL_NAMES:
        return None
    return (
        f"'{tool}' is not a callable tool — there is no such tool in AVAILABLE TOOLS. "
        f"'{tool}' is a top-level response TYPE, emitted as its own object — "
        f'{{"type":"{tool}", ...}} (see the "{tool}" variant above for its required '
        f'fields) — NEVER as {{"type":"tool_call","tool":"{tool}",...}}. If you are '
        "trying to make a change: type='edit' is already directly available — emit it "
        "now (Plan Mode is the only phase where you'd emit propose_mode first)."
    )


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


def _progress_repeat_correction(
    resp: dict[str, object], atype: str, last_was_progress: bool,
) -> str | None:
    """Reject a `progress` note that immediately follows another `progress` with no real
    action between — a weak model can turn a free non-terminal action into a narration
    attractor (spam notes instead of acting). Mirrors the emit_patch-dedup discipline;
    routes through the same _MAX_MALFORMED correction chain (no new retry primitive)."""
    if atype != "progress" or not last_was_progress:
        return None
    return (
        "You already posted a progress note and took no action after it. A progress note "
        "does NOT count as doing the work. Take the actual next action now — emit "
        "type='tool_call' / 'edit' / 'submit_changes' (or 'answer' if you are truly done)."
    )


def _progress_dedup_correction(
    resp: dict[str, object], atype: str, seen_notes: set[str],
) -> str | None:
    """Reject an exact-duplicate progress note already emitted this turn (same attractor
    class as _progress_repeat_correction, but catches non-adjacent repeats)."""
    if atype != "progress":
        return None
    note = _normalize_progress_note(resp)
    # An empty note is _empty_action_correction's business (it fires earlier in the
    # chain); never let "" match a stored value here.
    if note and note in seen_notes:
        return (
            "You already posted that exact progress note this turn. Do not repeat it — "
            "take the next real action instead (tool_call / edit / submit_changes / answer)."
        )
    return None


# Matches this workspace's writing-plans skill's own plan-doc convention, e.g.:
#   "REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
#    or superpowers:executing-plans to implement this plan task-by-task."
# One directive LINE can name more than one candidate (an "either/or" handoff choice,
# as above) — _REQUIRED_SUBSKILL_LINE_RE captures the rest of that line, then
# _SUPERPOWERS_NAME_RE pulls every `superpowers:<name>` mention out of it, so
# _extract_required_subskills returns all of them, in the order they appear.
_REQUIRED_SUBSKILL_LINE_RE = re.compile(r"REQUIRED SUB-SKILL:([^\n]*)", re.IGNORECASE)
_SUPERPOWERS_NAME_RE = re.compile(r"superpowers:([a-z0-9_-]+)", re.IGNORECASE)

# Crucible's chat controller has no subagent-dispatch tool (verified: no such tool
# exists anywhere in agentd/tools or agentd/skills) — a plan directive naming
# "subagent-driven-development" is never actually executable here, only its sibling
# "executing-plans" (single continuous session, no separate session needed) is. This
# is a fixed fact about THIS host, not a preference between the two skills.
_NON_EXECUTABLE_SUBSKILLS = frozenset({"subagent-driven-development"})


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
    (see _NON_EXECUTABLE_SUBSKILLS) — None if the directive named none, or named only
    ones this host can't run."""
    return next((n for n in names if n not in _NON_EXECUTABLE_SUBSKILLS), None)


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


@dataclass
class ControllerOutcome:
    kind: str  # "answer" | "clarify" | "propose_mode" | "submit_changes"
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
        # The live conversation list `_iterate` mutates — exposed via partial_history() so a
        # caller can persist what a CANCELLED turn (/stop) accumulated before the cancel
        # raised, instead of losing the turn's exploration + already-promoted edits (Q2).
        self._history: list[dict[str, object]] = []
        # Whether any edit has been applied this turn — half of the `active_entry` signal
        # (the other half is "no todo list yet"). While both hold, the payload builder shows
        # the clean entry hint (write_todos-as-tool_call) instead of the mid-turn reconcile
        # hint, so the first-action case isn't mis-routed.
        self._edit_applied = False

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
        """The action types legal THIS iteration — the phase SM's own set, plus a
        conditional propose_mode addition when in ACTIVE with the task subsystem on
        (I4: lets a big-enough ACTIVE-phase request escalate to a reviewed task
        without a detour through Plan Mode)."""
        types = list(self._sm.allowed_types())
        if self._sm.phase == "ACTIVE" and self._task_subsystem_enabled:
            types.append("propose_mode")
        return types

    def _allowed_modes_for_current_phase(self) -> frozenset[str]:
        return (
            self._active_allowed_modes if self._sm.phase == "ACTIVE"
            else self._plan_allowed_modes)

    def partial_history(self) -> list[dict[str, object]]:
        """The verbatim conversation accumulated so far this turn. Meaningful after a
        cancel: run()'s normal return persists history itself, but a CancelledError unwinds
        before that — the caller reads this to persist the partial."""
        return self._history

    async def run(
        self,
        plan_context: dict[str, object],
        *,
        max_iters: int = 32,
        seed_history: list[dict[str, object]] | None = None,
        auto_accept_edits: bool = False,
        edit_decision_cb: EditDecisionCb | None = None,
        edit_record_cb: EditRecordCb | None = None,
        retrieval_delta_cb: RetrievalDeltaCb | None = None,
        on_pills_update: PillsUpdateCb | None = None,
    ) -> ControllerOutcome:
        tool_defs = [d.model_dump() for d in self._registry.definitions()]
        history = [dict(m) for m in seed_history] if seed_history else []
        # Expose the live list NOW (before _iterate can raise) so a cancel mid-turn still
        # leaves the caller a readable partial (Q2). _iterate mutates this same object.
        self._history = history
        seen: dict[str, int] = {}
        # Bail only after this many CONSECUTIVE malformed responses (mirror PlanningLoop).
        _MAX_MALFORMED = 3
        consecutive_malformed = 0
        plan_context = {**plan_context, "max_iters": max_iters}
        # Tool trace + thinking accumulated across the turn → persisted as durable
        # pills + thinking entries (reload). Live SSE copies die on reload.
        self._calls = []
        self._results = []
        self._thinking = []
        self._edit_applied = False
        try:
            outcome = await self._iterate(
                plan_context, history, tool_defs, seen, max_iters,
                _MAX_MALFORMED, consecutive_malformed,
                auto_accept_edits=auto_accept_edits,
                edit_decision_cb=edit_decision_cb,
                edit_record_cb=edit_record_cb,
                retrieval_delta_cb=retrieval_delta_cb,
                on_pills_update=on_pills_update,
            )
            if outcome.tool_events is None and self._calls:
                outcome.tool_events = trace_to_tool_events(
                    AgentToolTrace(step_id="chat", calls=self._calls, results=self._results),
                    "execution")
            if outcome.thinking_log is None and self._thinking:
                outcome.thinking_log = list(self._thinking)
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
        auto_accept_edits: bool,
        edit_decision_cb: EditDecisionCb | None,
        edit_record_cb: EditRecordCb | None,
        retrieval_delta_cb: RetrievalDeltaCb | None,
        on_pills_update: PillsUpdateCb | None = None,
    ) -> ControllerOutcome:
        def _on_thinking(chunk: str) -> None:
            # Stream the model's reasoning live so the chat thinking pane updates
            # during a model call (the FE maps tool_thinking_chunk). Raw token
            # chunks are live-only; durable thinking_log gets compact tool labels.
            self._broadcaster.broadcast(self._channel_id, {
                "type": "tool_thinking_chunk", "payload": {"chunk": chunk}})

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
        last_was_progress = False
        seen_notes: set[str] = set()

        for iteration in range(max_iters + 1):
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
                history, run_id, query=str(plan_context.get("goal", "")))
            history[:] = _prep.history
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
            plan_context["active_entry"] = (
                self._sm.phase == "ACTIVE" and not self._ledger.items
                and not self._edit_applied and not plan_context.get("edit_is_resume"))
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
            try:
                resp = await self._reasoning.create_controller_step(
                    plan_context=plan_context, history=history,
                    tool_definitions=tool_defs, phase=self._sm.phase,
                    on_thinking=_on_thinking, on_retry=_on_retry,
                )
            except Exception as exc:
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
                        "Respond again with EXACTLY ONE complete JSON object matching the "
                        "schema — no prose, no markdown fences. If your response was cut off, "
                        "produce a SHORTER one (e.g. a smaller file, or split a large change "
                        "into more than one patch_ops entry across turns)."
                    ),
                })
                continue
            atype = str(resp.get("type", ""))
            logger.info("[controller] iter=%d phase=%s action=%s", iteration, self._sm.phase, atype)
            # Reject BEFORE dispatching: wrong action type for the phase, a propose_mode with
            # invalid mode vocabulary, OR a well-typed action with an empty REQUIRED field (the
            # flat schema permits {"type":"answer"} / empty tool_call — see
            # _empty_action_correction). Each is corrected + retried, bounded by _MAX_MALFORMED.
            correction = (
                MALFORMED_CORRECTION
                if atype not in self._allowed_action_types()
                else _propose_mode_correction(resp, self._allowed_modes_for_current_phase()) if atype == "propose_mode"
                else _reserved_tool_name_correction(resp, atype)
                or _decide_state_change_correction(resp, self._sm.phase)
                or _empty_action_correction(resp, atype)
                or _answer_intent_divergence_correction(resp, atype, tool_names)
                # After _empty_action_correction on purpose: a blank note must be
                # reported as EMPTY, not misdiagnosed as a duplicate of "".
                or _progress_repeat_correction(resp, atype, last_was_progress)
                or _progress_dedup_correction(resp, atype, seen_notes)
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
                history.append(assistant_turn(resp))
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
                # Live event (durable half is the cb below). Mirrors tool_call's
                # broadcast-live / persist-separately split.
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "chat_progress", "payload": {"note": note}})
                if self._progress_note_cb is not None:
                    try:
                        await self._progress_note_cb(note)
                    except Exception:  # noqa: BLE001 — a narration write must never kill a turn
                        logger.warning("[controller] progress_note_cb failed", exc_info=True)
                history.append(assistant_turn(resp))
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
                # call_index = the position this call will occupy in the trace (it's
                # appended below), which equals the persisted pill id (trace_to_tool_events
                # uses enumerate index). The FE uses it as the pill id so a switch-back
                # resume dedups replayed pills against the loaded in-flight message.
                call_index = len(self._calls)
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
                        pills = trace_to_tool_events(
                            AgentToolTrace(
                                step_id="chat", calls=self._calls, results=self._results),
                            "execution")
                        await on_pills_update(pills, list(self._thinking))
                    except Exception:
                        logger.debug("[controller] inflight pill persist failed", exc_info=True)
                history.append(assistant_turn(resp))
                history.append({"role": "tool_result", "tool": tool, "content": out.output})
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
                        "content": (
                            "That 'edit' had no patch_ops, so NOTHING was applied. If you meant to "
                            "create or update the TODO LIST, that is a TOOL CALL — emit "
                            '{"type":"tool_call","tool":"write_todos","args":{"items":[…]}}, NOT '
                            "type='edit'. To change a file, emit type='edit' with a NON-EMPTY "
                            "patch_ops (each op: file + its op fields). To finish, emit "
                            "type='submit_changes'.")})
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
                    # wait while the model thrashes. Log it AND surface a live + durable thinking
                    # line ("✗ edit failed: <reason>") so the failure is legible in agentd.log
                    # and the chat thinking pane.
                    reason_line = str(exc).splitlines()[0][:200] if str(exc) else "unknown error"
                    logger.info("[controller] edit FAILED phase=%s ops=%d: %s",
                                self._sm.phase, len(ops), reason_line)
                    self._thinking.append(f"✗ edit failed: {reason_line}")
                    self._broadcaster.broadcast(self._channel_id, {
                        "type": "chat_agent_thinking",
                        "payload": {"message": f"✗ edit failed: {reason_line}"}})
                    intent = {k: v for k, v in resp.items() if k != "patch_ops"}
                    history.append(assistant_turn(intent))
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"PATCH FAILED: {exc} Re-emit ONE corrected edit op — "
                                   "'file' is a workspace-relative path, code goes in 'content'."})
                    continue
                # Auto-accept (instant promote) OR hold for a per-edit review decision.
                # The decision cb holds the SSE stream open + renders the live diff via
                # the /live EditGate (review mode only). The loop does NOT broadcast
                # diff_ready — edit_record_cb is the single transcript writer (durable
                # diff_card + the auto-accept live render), so nothing dangles on reload.
                if auto_accept_edits or edit_decision_cb is None:
                    await self._edit.accept()
                    accepted = True
                    reason = ""
                else:
                    decision = await edit_decision_cb(diff)
                    accepted = decision.get("decision") == "accept"
                    reason = str(decision.get("reason", ""))
                    if accepted:
                        await self._edit.accept()
                    else:
                        await self._edit.reject()  # restore shadow from real (shadow==real)
                if edit_record_cb is not None:
                    await edit_record_cb(diff, "accept" if accepted else "reject", reason)
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
