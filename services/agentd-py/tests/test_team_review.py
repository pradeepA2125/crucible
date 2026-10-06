"""A review cycle's outcome and where each objection goes (spec v2 §8.7)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentd.teams.models import TeamMember, TeamPost
from agentd.teams.review import Objection, evaluate_review, route_objection


def _post(seq: int, author: str, kind: str, ref: str | None = None, text: str = "",
          payload: dict | None = None) -> TeamPost:
    return TeamPost(team_id="t", seq=seq, author=author, kind=kind, ref_id=ref, text=text,
                    payload=payload or {}, created_at=datetime.now(UTC))


MEMBERS = [
    TeamMember(team_id="t", agent_id="a", label="api",
               assignment={"member": "api", "part": "api", "files": ["a.py"]}),
    TeamMember(team_id="t", agent_id="b", label="tests",
               assignment={"member": "tests", "part": "tests", "files": ["t.py"]}),
    TeamMember(team_id="t", agent_id="c", label="review"),
]


def test_latest_stance_wins_and_silence_abstains() -> None:
    posts = [
        _post(9, "system", "proposal"),
        _post(10, "api", "object", "P9", "too slow", {"evidence": {"files": ["a.py"], "line": 3}}),
        _post(11, "api", "agree", "P9"),
        _post(12, "tests", "object", "P9", "missing case", {"evidence": {"files": ["t.py"]}}),
        _post(13, "tests", "agree", "P3"),           # another proposal: ignored
    ]
    outcome = evaluate_review(posts, "P9", ["api", "tests", "review"])
    assert outcome.stances == {"api": "agree", "tests": "object", "review": "none"}
    assert outcome.objections == [Objection("tests", 12, "missing case", ("t.py",))]
    assert outcome.abstained == ["review"]


def _route(objection: Objection, tmp_path: Path, **over):  # type: ignore[no-untyped-def]
    args = dict(workspace=tmp_path, approval_gate=False, plan_files={"a.py", "t.py"},
                can_edit=lambda _label: True)
    args.update(over)
    return route_objection(objection, MEMBERS, **args)


def test_an_owned_file_goes_to_its_owner(tmp_path) -> None:
    routing = _route(Objection("review", 5, "bug", ("docs.md", "a.py")), tmp_path)
    assert (routing.fixer, routing.synthetic) == ("api", False)


def test_an_unowned_file_goes_to_the_objector(tmp_path) -> None:
    (tmp_path / "util.py").write_text("x = 1\n")
    routing = _route(Objection("review", 5, "bug", ("util.py",)), tmp_path)
    assert (routing.fixer, routing.synthetic) == ("review", True)


def test_unroutable_objections_go_to_main(tmp_path) -> None:
    (tmp_path / "util.py").write_text("x = 1\n")
    cases = [
        (Objection("review", 5, "bug", ()), {}, "cites no file"),
        (Objection("review", 5, "bug", ("gone.py",)), {}, "does not exist"),
        (Objection("review", 5, "bug", ("util.py",)), {"can_edit": lambda _l: False},
         "cannot edit"),
        (Objection("review", 5, "bug", ("util.py",)), {"approval_gate": True},
         "outside the approved plan"),
    ]
    for objection, over, why in cases:
        routing = _route(objection, tmp_path, **over)
        assert routing.fixer is None and why in routing.why, (why, routing)


def test_protected_files_go_to_main(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("x\n")
    routing = _route(Objection("review", 5, "bug", ("AGENTS.md",)), tmp_path)
    assert routing.fixer is None and "protected" in routing.why
