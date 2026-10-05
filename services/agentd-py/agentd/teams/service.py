"""Board operations (spec v2 §7.3) and what a member reads (§7.6).

Phase 4 has no coordinator: the phase is whatever the team row says (DELIBERATING from
creation), and these methods only enforce the per-phase rules. Phase 5's coordinator drives
the phase and rounds; nothing here needs to change for it."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentd.subagents.framing import frame
from agentd.teams.models import LIVE_TEAM_PHASES, TeamActivity, TeamPost, TeamRecord
from agentd.teams.store import TeamStore
from agentd.teams.validation import (
    MAX_POSTS_PER_ACTIVATION,
    TeamInputError,
    check_text,
    effective_mentions,
    parse_proposal_id,
    validate_evidence,
)

_STANCE_KINDS = ("agree", "object")


logger = logging.getLogger(__name__)

@dataclass
class ActivationCounters:
    posts: int = 0
    team_mentions: int = 0


@dataclass(frozen=True)
class AgentInfo:
    name: str
    description: str
    status: str


class TeamService:
    def __init__(
        self, store: TeamStore, workspace: Path, agent_info: Callable[[str], AgentInfo],
        on_post: Callable[[TeamRecord, TeamPost], None] = lambda _t, _p: None,
        on_activity: Callable[[TeamRecord, TeamActivity], None] = lambda _t, _a: None,
    ) -> None:
        self._store = store
        self._workspace = workspace
        self._agent_info = agent_info
        self._on_post = on_post
        self._on_activity = on_activity

    # ── helpers ─────────────────────────────────────────────────────────────

    def _team(self, team_id: str) -> TeamRecord:
        team = self._store.get_team(team_id)
        if team is None:
            raise TeamInputError(f"no team {team_id!r}")
        if team.phase not in LIVE_TEAM_PHASES:
            raise TeamInputError(f"team {team.name!r} has ended ({team.phase})")
        return team

    def _roster(self, team_id: str) -> list[str]:
        return [m.label for m in self._store.members(team_id)]

    def _count(self, counters: ActivationCounters | None, mentions: list[str]) -> None:
        if counters is None:
            return
        if counters.posts >= MAX_POSTS_PER_ACTIVATION:
            raise TeamInputError(
                f"you have sent {MAX_POSTS_PER_ACTIVATION} posts and messages this activation "
                "(the limit) — report, or wait for replies")
        if "team" in mentions and counters.team_mentions >= 1:
            raise TeamInputError("only one @team post per activation — mention members by name")
        counters.posts += 1
        if "team" in mentions:
            counters.team_mentions += 1

    def _emit(self, team: TeamRecord, post: TeamPost) -> TeamPost:
        self._on_post(team, post)
        return post

    def _open_proposal(self, team_id: str, raw: object) -> TeamPost:
        seq = parse_proposal_id(raw)
        post = self._store.get_post(team_id, seq)
        if post is None or post.kind != "proposal":
            raise TeamInputError(f"P{seq} is not a proposal on this board")
        if post.closed is not None:
            raise TeamInputError(f"P{seq} is closed ({post.closed})")
        return post

    def _require_phase(self, team: TeamRecord, allowed: tuple[str, ...], action: str) -> None:
        if team.phase not in allowed:
            raise TeamInputError(
                f"{action} is not allowed while the team is {team.phase} "
                f"(allowed in: {', '.join(allowed)})")

    def _stance_phase(self, team: TeamRecord, proposal: TeamPost, action: str) -> None:
        if team.phase == "DELIBERATING":
            return
        if team.phase == "REVIEWING" and team.closing_proposal_id == proposal.proposal_id:
            return
        raise TeamInputError(
            f"{action} is not allowed while the team is {team.phase} (only in DELIBERATING, "
            "or on the closing proposal in REVIEWING)")

    def open_proposals(self, team_id: str) -> list[TeamPost]:
        return [p for p in self._store.posts(team_id) if p.kind == "proposal" and p.closed is None]

    def stances(self, team_id: str) -> dict[int, dict[str, str]]:
        out: dict[int, dict[str, str]] = {}
        for post in self._store.posts(team_id):
            if post.kind in _STANCE_KINDS and post.ref_id:
                out.setdefault(parse_proposal_id(post.ref_id), {})[post.author] = post.kind
        return out

    # ── operations ──────────────────────────────────────────────────────────

    def post(self, team_id: str, author: str, text: object, mentions: object = None,
             counters: ActivationCounters | None = None) -> TeamPost:
        team = self._team(team_id)
        body = check_text(text, "post")
        effective = effective_mentions(body, mentions, self._roster(team_id))
        self._count(counters, effective)
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="post", text=body, mentions=effective,
            round=team.round if team.phase == "DELIBERATING" else None))

    def message(self, team_id: str, author: str, member: object, text: object,
                counters: ActivationCounters | None = None) -> TeamPost:
        team = self._team(team_id)
        roster = self._roster(team_id)
        target = str(member or "").strip().lstrip("@").casefold()
        if target not in roster:
            raise TeamInputError(f"unknown member {member!r}; the team is: {', '.join(roster)}")
        if target == author:
            raise TeamInputError("you cannot message yourself")
        body = check_text(text, "message")
        self._count(counters, [])
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="post", text=body, recipient=target,
            mentions=[target]))

    def propose(self, team_id: str, author: str, text: object, assignments: object,
                shared_files: object = None, supersedes: object = None,
                counters: ActivationCounters | None = None) -> TeamPost:
        team = self._team(team_id)
        self._require_phase(team, ("DELIBERATING",), "team_propose")
        body = check_text(text, "proposal")
        roster = self._roster(team_id)
        owed = [p.proposal_id for p in self.open_proposals(team_id)
                if p.author != author and (p.round or 0) < team.round
                and author not in self.stances(team_id).get(p.seq, {})]
        if owed:
            raise TeamInputError(
                f"State your stance on {', '.join(owed)} first (team_agree, with a note for "
                "small changes, or team_object).")
        if not isinstance(assignments, list):
            raise TeamInputError("assignments must be a list of {member, part, files}")
        parts: list[dict[str, object]] = []
        for i, item in enumerate(assignments):
            if not isinstance(item, dict):
                raise TeamInputError(f"assignments[{i}] must be an object")
            member = str(item.get("member", "")).lstrip("@").casefold()
            if member not in roster:
                raise TeamInputError(
                    f"assignments[{i}]: unknown member {item.get('member')!r}; "
                    f"the team is: {', '.join(roster)}")
            files = item.get("files") or []
            if not isinstance(files, list):
                raise TeamInputError(f"assignments[{i}].files must be a list of paths")
            parts.append({"member": member, "part": check_text(item.get("part"), "part"),
                          "files": [str(f) for f in files]})
        shared = [str(f) for f in shared_files] if isinstance(shared_files, list) else []
        closes: list[TeamPost] = []
        if supersedes is not None:
            if not isinstance(supersedes, list):
                raise TeamInputError("supersedes must be a list of proposal ids")
            closes = [self._open_proposal(team_id, raw) for raw in supersedes]
        self._count(counters, [])
        post = self._store.append_post(
            team_id, author=author, kind="proposal", text=body, round=team.round,
            payload={"assignments": parts, "shared_files": shared,
                     "supersedes": [p.proposal_id for p in closes]})
        for old in closes:
            self._store.close_proposal(team_id, old.seq, "superseded")
        return self._emit(team, post)

    def agree(self, team_id: str, author: str, proposal_id: object,
              note: object = None) -> TeamPost:
        team = self._team(team_id)
        proposal = self._open_proposal(team_id, proposal_id)
        self._stance_phase(team, proposal, "team_agree")
        if proposal.author == author:
            raise TeamInputError("your own proposal already counts as your agreement")
        payload = {"note": check_text(note, "note")} if note not in (None, "") else {}
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="agree", text=str(payload.get("note", "")),
            ref_id=proposal.proposal_id, round=team.round, payload=payload))

    def object_(self, team_id: str, author: str, proposal_id: object, reason: object,
                evidence: object) -> TeamPost:
        team = self._team(team_id)
        proposal = self._open_proposal(team_id, proposal_id)
        self._stance_phase(team, proposal, "team_object")
        body = check_text(reason, "reason")
        assigned = {f for a in proposal.payload.get("assignments", []) for f in a.get("files", [])}
        assigned |= set(proposal.payload.get("shared_files", []))
        checked = validate_evidence(
            evidence, workspace=self._workspace, assignment_files=assigned,
            post_exists=lambda seq: self._store.get_post(team_id, seq) is not None)
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="object", text=body, ref_id=proposal.proposal_id,
            round=team.round, payload={"evidence": checked}))

    def withdraw(self, team_id: str, author: str, proposal_id: object) -> TeamPost:
        team = self._team(team_id)
        self._require_phase(team, ("DELIBERATING",), "team_withdraw")
        proposal = self._open_proposal(team_id, proposal_id)
        if proposal.author != author:
            raise TeamInputError(f"you can only withdraw your own proposals; "
                                 f"{proposal.proposal_id} is {proposal.author}'s")
        self._store.close_proposal(team_id, proposal.seq, "withdrawn")
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="withdraw", text="", ref_id=proposal.proposal_id,
            round=team.round))

    def read(self, team_id: str, viewer: str, since_seq: int = 0) -> list[TeamPost]:
        return self._store.posts(team_id, since_seq=since_seq, viewer=viewer)

    def system_post(self, team_id: str, text: str) -> TeamPost:
        team = self._store.get_team(team_id)
        assert team is not None
        return self._emit(team, self._store.append_post(
            team_id, author="system", kind="system", text=text))

    # ── what members and the main agent read ────────────────────────────────


    def record(
        self, team_id: str, label: str, kind: str, *, activation: int | None = None,
        cause_seq: int | None = None, payload: dict[str, Any] | None = None,
    ) -> TeamActivity | None:
        """Append one activity event and stream it (spec 2026-10-05 §4.3). Best-effort: the
        record is for the user's eyes; it must never fail the work it describes."""
        try:
            event = self._store.append_activity(
                team_id, label=label, kind=kind, activation=activation,
                cause_seq=cause_seq, payload=payload)
            team = self._store.get_team(team_id)
            if team is not None:
                self._on_activity(team, event)
            return event
        except Exception:  # noqa: BLE001 — best-effort by design (spec §10)
            logger.warning("[teams] could not record %s activity for %s in %s", kind, label,
                           team_id, exc_info=True)
            return None

    def _who(self, team_id: str, label: str) -> str:
        if label in ("main", "user", "system"):
            return label
        member = self._store.member(team_id, label)
        name = self._agent_info(member.agent_id).name if member else "?"
        return f"{label} ({name})"

    def _render_post(self, team_id: str, post: TeamPost, viewer: str) -> str:
        if post.kind == "system":
            return f"● system: {post.text}"   # system-written: never model text
        if post.kind == "proposal":
            kind = f"proposal {post.proposal_id}"
            lines = [post.text]
            for a in post.payload.get("assignments", []):
                files = ", ".join(a.get("files", [])) or "no files"
                lines.append(f"assignment — {a['member']}: {a['part']} ({files})")
            if post.payload.get("shared_files"):
                lines.append("shared files: " + ", ".join(post.payload["shared_files"]))
            if post.payload.get("supersedes"):
                lines.append("supersedes: " + ", ".join(post.payload["supersedes"]))
            body = "\n".join(lines)
        elif post.kind == "agree":
            kind = f"agrees with {post.ref_id}"
            body = post.text or "(no note)"
        elif post.kind == "object":
            kind = f"objects to {post.ref_id}"
            body = post.text + "\nevidence: " + json.dumps(post.payload.get("evidence", {}))
        elif post.kind == "withdraw":
            kind = f"withdraws {post.ref_id}"
            body = "(withdrawn)"
        elif post.recipient is not None:
            kind = "direct message to you" if post.recipient == viewer else (
                f"direct message to {post.recipient}")
            body = post.text
        else:
            kind = "board post"
            body = post.text
        return frame(self._who(team_id, post.author), kind, body, seq=post.seq)

    def _header(self, team: TeamRecord, first: bool) -> str:
        lines = [f"Team {team.name!r} — phase {team.phase}, round {team.round} of "
                 f"{team.max_rounds}."]
        if first:
            lines.append(f"Goal: {team.goal}")
        lines.append("Check the claims relevant to your role, then state your stance on each "
                     "open proposal (team_agree with a note for small changes, or team_object "
                     "with evidence). Post what others need to know; report when you are done.")
        return "\n".join(lines)

    def render_delta_posts(self, team_id: str, label: str) -> tuple[str, int, list[TeamPost]]:
        """The member's inbox delta, its top seq, and the posts by others it covers — what
        the member is 'handed' (the took_up / picked_up record, spec 2026-10-05 §4.2)."""
        team = self._store.get_team(team_id)
        member = self._store.member(team_id, label)
        assert team is not None and member is not None
        visible = self._store.posts(team_id, since_seq=member.delivered_seq, viewer=label)
        top = max((p.seq for p in visible), default=member.delivered_seq)
        others = [p for p in visible if p.author != label]
        body = "\n\n".join(self._render_post(team_id, p, label) for p in others)
        header = self._header(team, first=member.delivered_seq == 0)
        text = f"{header}\n\n{body}" if body else f"{header}\n\nNo new posts."
        return text, top, others

    def render_delta(self, team_id: str, label: str) -> tuple[str, int]:
        text, top, _ = self.render_delta_posts(team_id, label)
        return text, top

    def status_text(self, team_id: str, label: str) -> str:
        team = self._store.get_team(team_id)
        member = self._store.member(team_id, label)
        assert team is not None and member is not None
        stances = self.stances(team_id)
        lines = [f"team {team.name!r}: phase {team.phase}, round {team.round} of "
                 f"{team.max_rounds}", f"goal: {team.goal}", "roster:"]
        for m in self._store.members(team_id):
            info = self._agent_info(m.agent_id)
            you = " (you)" if m.label == label else ""
            lines.append(f"- {m.label}{you}: {info.name} — {info.description} [{info.status}]")
        open_ps = self.open_proposals(team_id)
        lines.append("open proposals:" if open_ps else "open proposals: none")
        for p in open_ps:
            mine = "yours" if p.author == label else stances.get(p.seq, {}).get(label, "none")
            lines.append(f"- {p.proposal_id} by {p.author} — your stance: {mine}")
        if member.assignment:
            lines.append(f"your assignment: {json.dumps(member.assignment)}")
        unread = self._store.posts(team_id, since_seq=member.delivered_seq, viewer=label)
        dms = sum(1 for p in unread if p.recipient == label)
        mentions = sum(1 for p in unread if p.recipient is None and
                       (label in p.mentions or "team" in p.mentions) and p.author != label)
        lines.append(f"unread: {dms} unread direct message{'s' if dms != 1 else ''}, "
                     f"{mentions} mention{'s' if mentions != 1 else ''}")
        return "\n".join(lines)

    def brief(self, team_id: str, label: str) -> str:
        """Goal + roster for a member's system prompt. Status is left out on purpose: it
        changes, and changing text here would break the cached prefix (status_text has it)."""
        # Not _team(): a late leftover activation may start after the team ended, and its
        # prompt must still build (the member then reports with nothing to do).
        team = self._store.get_team(team_id)
        if team is None:
            raise TeamInputError(f"no team {team_id!r}")
        lines = [f"Team {team.name!r}. Goal: {team.goal}", "Roster:"]
        for m in self._store.members(team_id):
            info = self._agent_info(m.agent_id)
            you = " (you)" if m.label == label else ""
            lines.append(f"- {m.label}{you}: {info.name} — {info.description}")
        return "\n".join(lines)

    def summary(self, team_id: str) -> dict[str, object]:
        team = self._store.get_team(team_id)
        if team is None:
            raise TeamInputError(f"no team {team_id!r}")
        stances = self.stances(team_id)
        return {
            "team_id": team.team_id, "name": team.name, "goal": team.goal, "phase": team.phase,
            "round": team.round, "max_rounds": team.max_rounds,
            "paused_reason": team.paused_reason,
            "members": [{"label": m.label, "agent_id": m.agent_id,
                         "name": (info := self._agent_info(m.agent_id)).name,
                         "description": info.description, "status": info.status}
                        for m in self._store.members(team_id)],
            "open_proposals": [
                {"id": p.proposal_id, "author": p.author, "text": p.text[:600],
                 "stances": stances.get(p.seq, {})}
                for p in self.open_proposals(team_id)],
            "usage": {"requests": team.requests, "budget": team.budget},
        }
