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
    "team_read"})
MAIN_TOOL_NAMES = frozenset({
    "create_team", "post_board", "team_status", "adopt_proposal", "resume_team", "disband_team"})
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
                 counters: ActivationCounters) -> None:
        self._svc = service
        self._team_id = team_id
        self._label = label
        self._counters = counters

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
            tool("team_propose", "Propose how the team does the work: the approach, and who "
                 "does which part with which files. shared_files may be edited by any "
                 "assignee. supersedes closes earlier proposals yours replaces.",
                 {"text": _STR, "assignments": _ASSIGNMENTS, "shared_files": _STRS,
                  "supersedes": _STRS}, ["text", "assignments"]),
            tool("team_agree", "Agree with an open proposal. A note carries a small change "
                 "or an open question without a competing proposal.",
                 {"proposal_id": _STR, "note": _STR}, ["proposal_id"]),
            tool("team_object", "Object to an open proposal, with evidence: files + line, "
                 "command + output, or quote_seq (a board post).",
                 {"proposal_id": _STR, "reason": _STR, "evidence": _EVIDENCE},
                 ["proposal_id", "reason", "evidence"]),
            tool("team_withdraw", "Withdraw one of your own open proposals.",
                 {"proposal_id": _STR}, ["proposal_id"]),
            tool("team_read", "Re-read board posts and your direct messages after since_seq "
                 "(all of them when omitted).", {"since_seq": {"type": "integer"}}, []),
        ]

    def owns(self, tool: str) -> bool:
        return tool in MEMBER_TOOL_NAMES

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        tid, me, n = self._team_id, self._label, self._counters
        try:
            if tool == "team_post":
                post = self._svc.post(tid, me, args.get("text"), args.get("mentions"), n)
                return _ok({"seq": post.seq, "mentions": post.mentions})
            if tool == "team_message":
                post = self._svc.message(tid, me, args.get("member"), args.get("text"), n)
                return _ok({"seq": post.seq, "to": post.recipient})
            if tool == "team_propose":
                post = self._svc.propose(tid, me, args.get("text"), args.get("assignments"),
                                         args.get("shared_files"), args.get("supersedes"), n)
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


def parse_create_team(
    args: dict[str, object], catalog: dict[str, AgentDefinition],
) -> CreateTeamRequest:
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
    mentions = (effective_mentions(text, kickoff.get("mentions"), roster)
                if kind == "post" else [])
    assignments = kickoff.get("assignments") or []
    if kind == "proposal" and not isinstance(assignments, list):
        raise TeamInputError("kickoff.assignments must be a list of {member, part, files}")
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
        kickoff_mentions=mentions)


def _resume_unavailable(_team_id: str, _extra: int | None) -> dict[str, object]:
    raise TeamInputError("resume_team is not available here")


@dataclass(frozen=True)
class MainTeamOps:
    create: Callable[[CreateTeamRequest], Awaitable[dict[str, object]]]
    resolve: Callable[[str], str]
    post: Callable[[str, str, object], dict[str, object]]
    status: Callable[[str], dict[str, object]]
    disband: Callable[[str], Awaitable[dict[str, object]]]
    adopt: Callable[[str, str], dict[str, object]]
    resume: Callable[[str, int | None], dict[str, object]] = _resume_unavailable


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
                "Start a team of agents that deliberate on a shared board and then implement "
                "together. Each member's role is its agent definition. kickoff \"proposal\" "
                "opens with your own plan for them to check; \"post\" asks the mentioned "
                "members to propose. Returns at once; the team runs in the background."),
                parameters={"type": _OBJ, "properties": {
                    "name": _STR, "goal": _STR, "members": {"type": "array", "items": member},
                    "approval_gate": {"type": "boolean"}, "max_rounds": {"type": "integer"},
                    "budget": {"type": "integer"}, "kickoff": kickoff},
                    "required": ["name", "goal", "members", "kickoff"]}),
            ToolDefinition(name="post_board", description=(
                "Post on a team's board as the main agent — how the user's requests reach "
                "the team. Mention members with @label. A post to a FAILED team (after a "
                "backend restart, for instance) reopens it where it stopped."),
                parameters={"type": _OBJ, "properties": {**team, "text": _STR,
                                                         "mentions": _STRS},
                            "required": ["team", "text"]}),
            ToolDefinition(name="team_status", description=(
                "A team's phase, round, members and their status, open proposals with each "
                "member's stance, and usage against its budget."),
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
            ToolDefinition(name="disband_team", description=(
                "End a team: stop its members and close its board."),
                parameters={"type": _OBJ, "properties": team, "required": ["team"]}),
        ]

    def owns(self, tool: str) -> bool:
        return tool in MAIN_TOOL_NAMES

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        try:
            if tool == "create_team":
                result = await self._ops.create(parse_create_team(args, self._catalog))
                self._created.add(str(result.get("team_id", "")))
                return _ok({**result, "note": BACKGROUND_NOTE})
            team_id = self._ops.resolve(str(args.get("team", "")))
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
