"""Team tools (spec v2 §7.1–§7.3). Member tools are prefixed team_ so a model cannot take
them for action types; the main agent's tools are separate and never offered to members.
Descriptions say what each tool does and when it fits — none is ranked above another."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentd.subagents.definitions import AgentDefinition
from agentd.teams.config import team_budget_per_member, team_max_budget, team_max_members
from agentd.teams.service import ActivationCounters, TeamService
from agentd.teams.validation import TeamInputError, check_label, check_text, effective_mentions
from agentd.tools.registry import ToolDefinition, ToolOutput

MEMBER_TOOL_NAMES = frozenset({
    "team_post", "team_message", "team_propose", "team_agree", "team_object", "team_withdraw",
    "team_read", "team_reply_main"})
MAIN_TOOL_NAMES = frozenset({
    "create_team", "post_board", "team_status", "team_report", "adopt_proposal", "resume_team",
    "disband_team", "start_team_implementation", "set_team_mode",
    "message_team_lead", "cancel_team_lead_request"})
BACKGROUND_NOTE = ("The team runs in the background and the user can watch its board. "
                   "If your todo list holds the work you just handed over, mark those items "
                   "'blocked' with the note 'delegated to team <name>' in one write_todos "
                   "(blocked items do not stop you from answering; a milestone tells you when to "
                   "mark them done). Answer the user now; milestones will wake you.")
_OBJ = "object"
_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
_ASSIGNMENTS = {"type": "array", "items": {"type": _OBJ, "properties": {
    "member": _STR, "part": _STR, "files": _STRS}, "required": ["member", "part", "files"]}}
_EVIDENCE = {"type": _OBJ, "properties": {
    "files": _STRS, "line": {"type": "integer"}, "command": _STR, "output": _STR,
    "quote_seq": {"type": "integer"}}}


def _ok(payload: dict[str, object]) -> ToolOutput:
    return ToolOutput(output=json.dumps(payload, indent=2))


class TeamToolSource:
    name = "team"

    def __init__(self, service: TeamService, team_id: str, label: str,
                 counters: ActivationCounters, *,
                 reply_main: Callable[[str, int | None], dict[str, object]] | None = None) -> None:
        self._svc = service
        self._team_id = team_id
        self._label = label
        self._counters = counters
        self._reply_main = reply_main
        self._unsolicited_reply_sent = False

    def definitions(self) -> list[ToolDefinition]:
        def tool(name: str, description: str, props: dict[str, object],
                 required: list[str]) -> ToolDefinition:
            return ToolDefinition(name=name, description=description, parameters={
                "type": _OBJ, "properties": props, "required": required})
        return [
            tool("team_post", "Post on the team board, which every member reads. Mention "
                 "members with @label (or the mentions list) to bring a post to their "
                 "attention; @team reaches everyone.",
                 {"text": _STR, "mentions": _STRS}, ["text"]),
            tool("team_message", "Send one member a direct message only they (and the user) "
                 "see — for something that concerns one person.",
                 {"member": _STR, "text": _STR}, ["member", "text"]),
            tool("team_propose", "Propose how the team does the work (the lead only): the "
                 "approach, and who does which part with which files. shared_files may be "
                 "edited by any assignee. A new proposal replaces the open one.",
                 {"text": _STR, "assignments": _ASSIGNMENTS, "shared_files": _STRS},
                 ["text", "assignments"]),
            tool("team_agree", "Agree with an open proposal. A note carries a small change "
                 "or an open question without a competing proposal.",
                 {"proposal_id": _STR, "note": _STR}, ["proposal_id"]),
            tool("team_object", "Object to an open proposal, with evidence: files + line, "
                 "command + output, or quote_seq (a board post).",
                 {"proposal_id": _STR, "reason": _STR, "evidence": _EVIDENCE},
                 ["proposal_id", "reason", "evidence"]),
            tool("team_withdraw", "Withdraw one of your own open proposals.",
                 {"proposal_id": _STR}, ["proposal_id"]),
            tool("team_reply_main", "Reply privately to main's pending request (lead only). "
                 "This resolves communication; it does not complete your lifecycle report or "
                 "wake other members.",
                 {"text": _STR, "request_seq": {"type": "integer"}}, ["text"]),
            tool("team_read", "Re-read board posts and your direct messages after since_seq "
                 "(all of them when omitted).", {"since_seq": {"type": "integer"}}, []),
        ]

    def owns(self, tool: str) -> bool:
        return tool in MEMBER_TOOL_NAMES

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        tid, me, n = self._team_id, self._label, self._counters
        try:
            if tool == "team_reply_main":
                if self._reply_main is None:
                    raise TeamInputError("team_reply_main is only available to the lead "
                                         "with a pending request")
                seq = args.get("request_seq")
                if seq is not None and (not isinstance(seq, int) or
                                        isinstance(seq, bool) or seq < 1):
                    raise TeamInputError("request_seq must be a positive integer")
                text = check_text(args.get("text"), "text")
                if seq is None and self._unsolicited_reply_sent:
                    raise TeamInputError("only one unsolicited question to main per activation")
                self._svc._count(n, [])
                try:
                    result = self._reply_main(text, seq)
                except Exception:
                    n.posts -= 1
                    raise
                if seq is None:
                    self._unsolicited_reply_sent = True
                return _ok(result)
            if tool == "team_post":
                post = self._svc.post(tid, me, args.get("text"), args.get("mentions"), n)
                return _ok({"seq": post.seq, "mentions": post.mentions})
            if tool == "team_message":
                post = self._svc.message(tid, me, args.get("member"), args.get("text"), n)
                return _ok({"seq": post.seq, "to": post.recipient})
            if tool == "team_propose":
                post = self._svc.propose(tid, me, args.get("text"), args.get("assignments"),
                                         args.get("shared_files"), None, n)
                return _ok({"proposal_id": post.proposal_id, "seq": post.seq})
            if tool == "team_agree":
                post = self._svc.agree(tid, me, args.get("proposal_id"), args.get("note"))
                return _ok({"seq": post.seq, "agreed": post.ref_id})
            if tool == "team_object":
                post = self._svc.object_(tid, me, args.get("proposal_id"), args.get("reason"),
                                         args.get("evidence"))
                return _ok({"seq": post.seq, "objected": post.ref_id})
            if tool == "team_withdraw":
                post = self._svc.withdraw(tid, me, args.get("proposal_id"))
                return _ok({"seq": post.seq, "withdrawn": post.ref_id})
            if tool == "team_read":
                since = args.get("since_seq")
                posts = self._svc.read(tid, me, since if isinstance(since, int) else 0)
                rendered = [self._svc._render_post(tid, p, me) for p in posts]
                return ToolOutput(output="\n\n".join(rendered) or "No posts.")
        except TeamInputError as exc:
            return ToolOutput(output=f"Error: {exc}", is_error=True)
        return ToolOutput(output=f"Error: unknown tool {tool!r}", is_error=True)


@dataclass(frozen=True)
class TeamMemberSpec:
    label: str
    agent: AgentDefinition


@dataclass(frozen=True)
class CreateTeamRequest:
    name: str
    goal: str
    members: list[TeamMemberSpec]
    approval_gate: bool
    max_rounds: int
    budget: int
    kickoff_kind: str
    kickoff_text: str
    kickoff_assignments: list[dict[str, object]]
    kickoff_shared_files: list[str]
    kickoff_mentions: list[str]
    lead: str | None = None
    mode: str | None = None
    deliverable_kind: str = "recommendation"
    deliverable_description: str = ""


def parse_create_team(
    args: dict[str, object], catalog: dict[str, AgentDefinition],
) -> CreateTeamRequest:
    mode = args.get("mode")
    if mode not in ("deliberate", "deliberate_and_implement", "implement"):
        raise TeamInputError("mode must be deliberate, deliberate_and_implement, or implement")
    deliverable = args.get("deliverable")
    if not isinstance(deliverable, dict):
        raise TeamInputError("deliverable must be {kind, description}")
    deliverable_kind = check_text(deliverable.get("kind"), "deliverable.kind")
    deliverable_description = check_text(deliverable.get("description"), "deliverable.description")
    name = check_text(args.get("name"), "name")[:80]
    goal = check_text(args.get("goal"), "goal")
    raw_members = args.get("members")
    limit = team_max_members()
    if not isinstance(raw_members, list) or not 2 <= len(raw_members) <= limit:
        raise TeamInputError(f"a team has 2 to {limit} members")
    members: list[TeamMemberSpec] = []
    for i, item in enumerate(raw_members):
        if not isinstance(item, dict):
            raise TeamInputError(f"members[{i}] must be {{label, agent}}")
        label = check_label(str(item.get("label", "")))
        if label in ("main", "user", "system", "team"):
            raise TeamInputError(f"label {label!r} is reserved")
        if any(m.label == label for m in members):
            raise TeamInputError(f"duplicate label {label!r}")
        agent = catalog.get(str(item.get("agent", "")))
        if agent is None:
            raise TeamInputError(f"members[{i}]: unknown agent {item.get('agent')!r}; "
                                 f"available: {', '.join(catalog)}")
        members.append(TeamMemberSpec(label, agent))
    kickoff = args.get("kickoff")
    if not isinstance(kickoff, dict) or kickoff.get("kind") not in ("proposal", "post"):
        raise TeamInputError('kickoff.kind must be "proposal" or "post"')
    kind = str(kickoff["kind"])
    text = check_text(kickoff.get("text"), "kickoff.text")
    roster = [m.label for m in members]
    lead = str(args.get("lead") or "").strip().lstrip("@").casefold()
    if lead not in roster:
        raise TeamInputError(f"lead must be one of the members: {', '.join(roster)}")
    mentions = (effective_mentions(text, kickoff.get("mentions"), roster)
                if kind == "post" else [])
    if mentions and lead not in mentions:
        mentions = [*mentions, lead]   # round 1 always starts the lead
    assignments = kickoff.get("assignments") or []
    if kind == "proposal" and not isinstance(assignments, list):
        raise TeamInputError("kickoff.assignments must be a list of {member, part, files}")
    if any(not isinstance(a, dict) for a in assignments):
        raise TeamInputError("kickoff.assignments must contain objects")
    if mode == "implement" and (kind != "proposal" or not assignments):
        raise TeamInputError("implement mode requires a proposal kickoff with nonempty assignments")
    raw_rounds = args.get("max_rounds")
    max_rounds = raw_rounds if isinstance(raw_rounds, int) else (3 if kind == "proposal" else 4)
    if not 1 <= max_rounds <= 6:
        raise TeamInputError("max_rounds must be between 1 and 6")
    cap = team_max_budget()
    raw_budget = args.get("budget")
    budget = raw_budget if isinstance(raw_budget, int) else min(
        team_budget_per_member() * len(members), cap)
    if not 1 <= budget <= cap:
        raise TeamInputError(f"budget must be between 1 and {cap} requests")
    shared = kickoff.get("shared_files")
    return CreateTeamRequest(
        name=name, goal=goal, members=members, approval_gate=bool(args.get("approval_gate")),
        max_rounds=max_rounds, budget=budget, kickoff_kind=kind, kickoff_text=text,
        kickoff_assignments=[a for a in assignments if isinstance(a, dict)],
        kickoff_shared_files=[str(f) for f in shared] if isinstance(shared, list) else [],
        kickoff_mentions=mentions, lead=lead, mode=str(mode),
        deliverable_kind=deliverable_kind, deliverable_description=deliverable_description)


def _resume_unavailable(_team_id: str, _extra: int | None) -> dict[str, object]:
    raise TeamInputError("resume_team is not available here")


def _report_unavailable(team_id: str) -> dict[str, object]:
    raise TeamInputError("team_report is not available here")


async def _operation_unavailable(_team_id: str, _args: dict[str, object]) -> dict[str, object]:
    raise TeamInputError("this team operation is not available here")


TeamOperation = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


@dataclass(frozen=True)
class MainTeamOps:
    create: Callable[[CreateTeamRequest], Awaitable[dict[str, object]]]
    resolve: Callable[[str], str]
    post: Callable[[str, str, object], dict[str, object]]
    status: Callable[[str], dict[str, object]]
    disband: Callable[[str], Awaitable[dict[str, object]]]
    adopt: Callable[[str, str], dict[str, object]]
    resume: Callable[[str, int | None], dict[str, object]] = _resume_unavailable
    report: Callable[[str], dict[str, object]] = _report_unavailable
    start_implementation: TeamOperation = _operation_unavailable
    set_mode: TeamOperation = _operation_unavailable
    message_lead: TeamOperation = _operation_unavailable
    cancel_lead_request: TeamOperation = _operation_unavailable
    can_execute: Callable[[], bool] = lambda: True


class MainTeamToolSource:
    name = "teams"

    def __init__(self, catalog: dict[str, AgentDefinition], ops: MainTeamOps, *,
                 first_turn_team_ids: set[str]) -> None:
        self._catalog = catalog
        self._ops = ops
        # Teams created in this turn: their team_status repeats the background note.
        self._created = first_turn_team_ids

    def definitions(self) -> list[ToolDefinition]:
        member = {"type": _OBJ, "properties": {"label": _STR, "agent": {
            "type": "string", "enum": list(self._catalog)}}, "required": ["label", "agent"]}
        kickoff = {"type": _OBJ, "properties": {
            "kind": {"type": "string", "enum": ["proposal", "post"]}, "text": _STR,
            "assignments": _ASSIGNMENTS, "shared_files": _STRS, "mentions": _STRS},
            "required": ["kind", "text"]}
        team = {"team": _STR}
        return [
            ToolDefinition(name="create_team", description=(
                "Start a team with explicit mode deliberate, deliberate_and_implement, "
                "or implement. "
                "Deliberate ends with the requested deliverable; consensus never authorizes "
                "execution. Each member's role is its agent definition. lead is the member who "
                "writes and revises the plan — the only one who proposes; pick the member whose "
                "role owns the overall design. kickoff \"proposal\" opens with your own plan for "
                "them to check; \"post\" asks the lead to propose. Returns at once; the team runs "
                "in the background."),
                parameters={"type": _OBJ, "properties": {
                    "name": _STR, "goal": _STR, "lead": _STR,
                    "mode": {"type": "string",
                             "enum": ["deliberate", "deliberate_and_implement", "implement"]},
                    "deliverable": {"type": _OBJ, "properties": {"kind": _STR, "description": _STR},
                                    "required": ["kind", "description"]},
                    "members": {"type": "array", "items": member},
                    "approval_gate": {"type": "boolean"}, "max_rounds": {"type": "integer"},
                    "budget": {"type": "integer"}, "kickoff": kickoff},
                    "required": ["name", "goal", "lead", "members", "kickoff",
                                 "mode", "deliverable"]}),
            ToolDefinition(name="post_board", description=(
                "Post on a team's board as the main agent — how the user's requests reach "
                "the team. Mention members with @label. A post to a FAILED team (after a "
                "backend restart, for instance) reopens it where it stopped. A post to a DONE "
                "team reopens it for a follow-up request: its members keep their context and "
                "it gets a fresh budget."),
                parameters={"type": _OBJ, "properties": {**team, "text": _STR,
                                                         "mentions": _STRS},
                            "required": ["team", "text"]}),
            ToolDefinition(name="team_status", description=(
                "A team's phase, round, members and their status, open proposals with each "
                "member's stance, and usage against its budget."),
                parameters={"type": _OBJ, "properties": team, "required": ["team"]}),
            ToolDefinition(name="team_report", description=(
                "What a team produced: the adopted and closing proposals in full, each "
                "member's final report, and objections to either proposal. Milestones carry "
                "only summaries; this gives the texts."),
                parameters={"type": _OBJ, "properties": team, "required": ["team"]}),
            ToolDefinition(name="adopt_proposal", description=(
                "Adopt one of a DEADLOCKED team's open proposals, as if every member agreed."),
                parameters={"type": _OBJ, "properties": {**team, "proposal_id": _STR},
                            "required": ["team", "proposal_id"]}),
            ToolDefinition(name="resume_team", description=(
                "Resume a PAUSED team, optionally raising its request budget."),
                parameters={"type": _OBJ, "properties": {**team,
                                                         "extra_budget": {"type": "integer"}},
                            "required": ["team"]}),
            ToolDefinition(name="start_team_implementation", description=(
                "Explicitly extend a completed actionable deliberation result into execution. "
                "Use its expected_run_id and result_seq; optional assignments replace "
                "the proposed owners. "
                "Requires execution authority and no unresolved private lead request."),
                parameters={
                    "type": _OBJ, "properties": {**team, "expected_run_id": {"type": "integer"},
                    "result_seq": {"type": "integer"}, "assignments": _ASSIGNMENTS,
                    "shared_files": _STRS, "approval_gate": {"type": "boolean"}},
                    "required": ["team", "expected_run_id", "result_seq"]}),
            ToolDefinition(name="set_team_mode", description=(
                "Narrow a team's current run to deliberate. Stops new execution; "
                "does not undo changes."),
                parameters={"type": _OBJ, "properties": {**team,
                    "expected_run_id": {"type": "integer"},
                    "mode": {"type": "string", "enum": ["deliberate"]},
                    "reason": _STR}, "required": ["team", "expected_run_id", "mode", "reason"]}),
            ToolDefinition(name="message_team_lead", description=(
                "Send a private request to the team's lead. Only the lead is activated; "
                "no board post "
                "or execution authorization. Retry a blocked request with retry_request_seq."),
                parameters={"type": _OBJ, "properties": {**team, "text": _STR,
                    "expected_run_id": {"type": "integer"}, "extra_budget": {"type": "integer"},
                    "retry_request_seq": {"type": "integer"}},
                    "required": ["team", "text", "expected_run_id"]}),
            ToolDefinition(name="cancel_team_lead_request", description=(
                "Cancel one unresolved private lead request before changing runs "
                "or asking a replacement question."),
                parameters={"type": _OBJ, "properties": {**team,
                    "expected_run_id": {"type": "integer"}, "request_seq": {"type": "integer"}},
                    "required": ["team", "expected_run_id", "request_seq"]}),
            ToolDefinition(name="disband_team", description=(
                "End a team: stop its members and close its board."),
                parameters={"type": _OBJ, "properties": team, "required": ["team"]}),
        ]

    def owns(self, tool: str) -> bool:
        return tool in MAIN_TOOL_NAMES

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        try:
            if tool == "create_team":
                req = parse_create_team(args, self._catalog)
                if req.mode != "deliberate" and not self._ops.can_execute():
                    raise TeamInputError("PLAN mode only permits deliberate teams; "
                                         "execution requires execution authority")
                result = await self._ops.create(req)
                self._created.add(str(result.get("team_id", "")))
                return _ok({**result, "note": BACKGROUND_NOTE})
            team_id = self._ops.resolve(str(args.get("team", "")))
            operations = {
                "start_team_implementation": self._ops.start_implementation,
                "set_team_mode": self._ops.set_mode,
                "message_team_lead": self._ops.message_lead,
                "cancel_team_lead_request": self._ops.cancel_lead_request,
            }
            if tool in operations:
                run_id = args.get("expected_run_id")
                if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
                    raise TeamInputError("expected_run_id must be a positive integer")
                if tool == "start_team_implementation" and not self._ops.can_execute():
                    raise TeamInputError("PLAN mode cannot start team implementation")
                if tool == "set_team_mode":
                    if args.get("mode") != "deliberate":
                        raise TeamInputError("set_team_mode only narrows to deliberate")
                    check_text(args.get("reason"), "reason")
                if tool == "message_team_lead":
                    check_text(args.get("text"), "text")
                for key in ("result_seq", "request_seq", "retry_request_seq", "extra_budget"):
                    value = args.get(key)
                    if value is not None and (not isinstance(value, int) or
                                              isinstance(value, bool) or value < 1):
                        raise TeamInputError(f"{key} must be a positive integer")
                required_seq = {"start_team_implementation": "result_seq",
                                "cancel_team_lead_request": "request_seq"}.get(tool)
                if required_seq and args.get(required_seq) is None:
                    raise TeamInputError(f"{required_seq} is required")
                return _ok(await operations[tool](team_id, args))
            if tool == "post_board":
                posted = self._ops.post(team_id, check_text(args.get("text"), "text"),
                                        args.get("mentions"))
                raw = posted.get("mentions")
                mentioned = [str(m) for m in raw] if isinstance(raw, list) else []
                # Say who it reached: a bare {seq, phase} read as "nothing changed", and the
                # main agent posted the same restart again and again (live 2026-10-07).
                if mentioned:
                    reach = (", ".join(f"@{m}" for m in mentioned) + " get it as their next "
                             "input (an idle member is woken; while the team deliberates it "
                             "arrives at the next round)")
                else:
                    reach = "Members read it with their next input"
                return _ok({**posted, "note": f"Posted. {reach}. The team reports at its next "
                                              "milestone."})
            if tool == "team_report":
                return _ok(self._ops.report(team_id))
            status = self._ops.status(team_id)
            if tool == "team_status":
                if team_id in self._created:
                    status = {**status, "note": BACKGROUND_NOTE}
                return _ok(status)
            if tool == "adopt_proposal":
                if status.get("phase") != "DEADLOCKED":
                    raise TeamInputError(f"adopt_proposal is only for a DEADLOCKED team; this "
                                         f"team is {status.get('phase')}")
                return _ok(self._ops.adopt(team_id, str(args.get("proposal_id", ""))))
            if tool == "resume_team":
                if status.get("phase") != "PAUSED":
                    raise TeamInputError(f"resume_team is only for a PAUSED team; this team is "
                                         f"{status.get('phase')}")
                extra = args.get("extra_budget")
                return _ok(self._ops.resume(team_id, extra if isinstance(extra, int) else None))
            if tool == "disband_team":
                return _ok(await self._ops.disband(team_id))
        except TeamInputError as exc:
            return ToolOutput(output=f"Error: {exc}", is_error=True)
        return ToolOutput(output=f"Error: unknown tool {tool!r}", is_error=True)
