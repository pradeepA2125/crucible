"""Team input validation (spec v2 §7.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.teams.validation import (
    TeamInputError,
    check_label,
    check_text,
    effective_mentions,
    parse_proposal_id,
    validate_evidence,
)

ROSTER = ["alice", "bob", "carol"]


def test_labels() -> None:
    assert check_label("api-1") == "api-1"
    for bad in ("Alice", "a b", "", "x" * 33, "@bob"):
        with pytest.raises(TeamInputError):
            check_label(bad)


def test_text_limits() -> None:
    assert check_text("  hi  ", "post") == "hi"
    for bad in ("", "   ", None, "x" * 8001):
        with pytest.raises(TeamInputError):
            check_text(bad, "post")


def test_mentions_union_casefold_and_team() -> None:
    assert effective_mentions("Ping @Bob and @team", ["@carol"], ROSTER) == ["bob", "carol", "team"]
    assert effective_mentions("mail me at a@b.com", None, ROSTER) == []


def test_unknown_explicit_mention_lists_roster() -> None:
    with pytest.raises(TeamInputError, match="alice, bob, carol"):
        effective_mentions("hi", ["dave"], ROSTER)


def test_proposal_ids() -> None:
    assert [parse_proposal_id(x) for x in ("P3", "p12", 7)] == [3, 12, 7]
    for bad in ("Q3", "P", "", None, "P-1"):
        with pytest.raises(TeamInputError):
            parse_proposal_id(bad)


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("one\ntwo\nthree\n")
    return tmp_path


def test_evidence_file_and_line(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ev = validate_evidence({"files": ["src/a.py"], "line": 2}, workspace=ws,
                           assignment_files=set(), post_exists=lambda s: False)
    assert ev == {"files": ["src/a.py"], "line": 2}


def test_evidence_command_output_and_quote(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    out = validate_evidence({"command": "pytest", "output": "1 failed"}, workspace=ws,
                            assignment_files=set(), post_exists=lambda s: False)
    assert out["command"] == "pytest"
    assert validate_evidence({"quote_seq": 4}, workspace=ws, assignment_files=set(),
                             post_exists=lambda s: s == 4) == {"quote_seq": 4}


@pytest.mark.parametrize("raw,needle", [
    ({}, "at least one of"),
    ({"files": ["src/a.py"]}, "at least one of"),
    ({"files": ["../outside.py"], "line": 1}, "outside the workspace"),
    ({"files": ["src/missing.py"], "line": 1}, "does not exist"),
    ({"files": ["src/a.py"], "line": 9}, "has 3 lines"),
    ({"quote_seq": 99}, "no post"),
    ("not a dict", "must be an object"),
])
def test_evidence_refusals(tmp_path: Path, raw, needle) -> None:
    with pytest.raises(TeamInputError, match=needle):
        validate_evidence(raw, workspace=_ws(tmp_path), assignment_files=set(),
                          post_exists=lambda s: False)


def test_evidence_may_name_a_file_the_proposal_assigns(tmp_path: Path) -> None:
    ev = validate_evidence({"files": ["src/new.py"], "line": 1}, workspace=_ws(tmp_path),
                           assignment_files={"src/new.py"}, post_exists=lambda s: False)
    assert ev["files"] == ["src/new.py"]
