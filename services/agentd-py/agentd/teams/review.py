"""Review (spec v2 §8.7) — pure: what a review cycle came to, and where each objection
goes. The coordinator does the posting and waking."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentd.chat.protected_paths import is_protected
from agentd.teams.models import TeamMember, TeamPost


@dataclass(frozen=True)
class Objection:
    label: str
    seq: int
    reason: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class ReviewOutcome:
    stances: dict[str, str]          # reviewer → agree | object | none
    objections: list[Objection]
    abstained: list[str]


@dataclass(frozen=True)
class Routing:
    objection: Objection
    fixer: str | None                # None → the main agent, in a member_blocked milestone
    synthetic: bool                  # the objector fixes files nobody owns
    why: str


def evaluate_review(posts: list[TeamPost], closing_id: str,
                    reviewers: list[str]) -> ReviewOutcome:
    latest: dict[str, TeamPost] = {}
    for post in sorted(posts, key=lambda p: p.seq):
        if post.kind in ("agree", "object") and post.ref_id == closing_id \
                and post.author in reviewers:
            latest[post.author] = post
    stances = {r: latest[r].kind if r in latest else "none" for r in reviewers}
    objections = []
    for label in reviewers:
        stance = latest.get(label)
        if stance is not None and stance.kind == "object":
            evidence = stance.payload.get("evidence") or {}
            files = tuple(str(f) for f in (evidence.get("files") or []))
            objections.append(Objection(label, stance.seq, stance.text, files))
    return ReviewOutcome(stances, objections, [r for r in reviewers if stances[r] == "none"])


def route_objection(
    objection: Objection, members: list[TeamMember], *, workspace: Path, approval_gate: bool,
    plan_files: set[str], can_edit: Callable[[str], bool],
) -> Routing:
    owners = {str(f): m.label for m in members if m.assignment
              for f in (m.assignment.get("files") or [])}
    for path in objection.files:
        if path in owners:
            return Routing(objection, owners[path], False, f"{path} is {owners[path]}'s file")
    if not objection.files:
        return Routing(objection, None, False, "the objection cites no file")
    root = workspace.resolve()
    for path in objection.files:
        if not (root / path).is_file():
            return Routing(objection, None, False, f"{path} does not exist")
        if is_protected(path):
            return Routing(objection, None, False, f"{path} is a protected file")
        if approval_gate and path not in plan_files:
            return Routing(objection, None, False, f"{path} is outside the approved plan")
    if not can_edit(objection.label):
        return Routing(objection, None, False, f"{objection.label} cannot edit files")
    return Routing(objection, objection.label, True,
                   "nobody owns the files: the objector fixes them")
