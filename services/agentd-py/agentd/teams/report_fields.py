"""A team member's report fields (spec v2 §8.3): stances and a proposal, validated before
the report is accepted and posted exactly as the tools would post them."""
from __future__ import annotations

import json
from collections.abc import Callable

from agentd.chat.controller_loop import ReportVerdict
from agentd.teams.service import ActivationCounters, PreparedPost, TeamService
from agentd.teams.validation import TeamInputError, parse_proposal_id

_SHAPE = ('"stances": [{"proposal_id": "P1", "stance": "agree", "note": "what you checked"}] '
          'or [{"proposal_id": "P1", "stance": "object", "reason": "...", '
          '"evidence": {"files": ["path"], "line": 12}}]')


class ReportFields:
    """One per member activation: the missing-stance redirect is used at most once."""

    def __init__(self, service: TeamService, team_id: str, label: str,
                 counters: ActivationCounters,
                 on_dropped: Callable[[list[str]], None] = lambda _errors: None) -> None:
        self._svc = service
        self._team_id = team_id
        self._label = label
        self._counters = counters
        self._on_dropped = on_dropped
        self._redirected = False
        self._last_refused: str | None = None

    def __call__(self, resp: dict[str, object], final: bool) -> ReportVerdict:
        errors: list[str] = []
        prepared: list[PreparedPost] = []
        stated: set[int] = set()
        raw = resp.get("stances") or []
        if not isinstance(raw, list):
            errors.append("stances must be a list")
            raw = []
        for i, entry in enumerate(raw):
            try:
                if not isinstance(entry, dict):
                    raise TeamInputError("must be an object")
                stance = entry.get("stance")
                if stance == "agree":
                    p = self._svc.prepare_agree(self._team_id, self._label,
                                                entry.get("proposal_id"), entry.get("note"))
                elif stance == "object":
                    p = self._svc.prepare_object(self._team_id, self._label,
                                                 entry.get("proposal_id"), entry.get("reason"),
                                                 entry.get("evidence"))
                else:
                    raise TeamInputError('stance must be "agree" or "object"')
                prepared.append(p)
                assert p.ref_id is not None
                stated.add(parse_proposal_id(p.ref_id))
            except TeamInputError as exc:
                ref = entry.get("proposal_id") if isinstance(entry, dict) else entry
                errors.append(f"stances[{i}] ({ref}): {exc}")
        proposal = resp.get("proposal")
        if proposal is not None:
            try:
                if not isinstance(proposal, dict):
                    raise TeamInputError("must be {text, assignments, shared_files?, supersedes?}")
                prepared.append(self._svc.prepare_propose(
                    self._team_id, self._label, proposal.get("text"),
                    proposal.get("assignments"), proposal.get("shared_files"),
                    proposal.get("supersedes"), also_stated=frozenset(stated)))
            except TeamInputError as exc:
                errors.append(f"proposal: {exc}")
        if errors and not final:
            key = json.dumps(resp, sort_keys=True, default=str)
            malformed = key == self._last_refused
            self._last_refused = key
            return ReportVerdict(
                message=("report REFUSED — " + "; ".join(errors)
                         + ". Fix these entries (or leave them out) and report again."),
                malformed=malformed)
        missing = [pid for pid in self._svc.expected_stances(self._team_id, self._label)
                   if parse_proposal_id(pid) not in stated]
        if missing and not final and not self._redirected:
            self._redirected = True
            return ReportVerdict(message=(
                f"Your report has no stance on {', '.join(missing)}. State one for each in "
                f"this report's stances field — {_SHAPE} — then report again. If you could "
                "not check something, agree and say so in the note, or object with what "
                "you found."))
        for p in prepared:
            try:
                self._svc.commit(self._team_id, self._label, p, self._counters,
                                 count=p.kind == "proposal")
            except TeamInputError as exc:
                errors.append(f"{p.kind}: {exc}")
        if errors:
            self._on_dropped(errors)
        return ReportVerdict()
