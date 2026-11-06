"""Trajectory cases, the fixture repo and the deterministic checks (observability spec §6.3)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from evals.injection.scoring import DiffRecord
from evals.trajectory.cases import CASES_DIR, FIXTURES_DIR, Case, CaseError, Check, load_cases
from evals.trajectory.checks import run_checks


def test_all_checked_in_cases_load() -> None:
    cases = load_cases()
    ids = [c.id for c in cases]
    assert len(ids) == 8 and len(set(ids)) == 8
    assert {"fix-discount", "compaction", "dispatch", "explain"} <= set(ids)
    compaction = next(c for c in cases if c.id == "compaction")
    assert compaction.flags["CRUCIBLE_MEMORY_WINDOW_TOKENS"] == "32000"
    assert all(c.workspace.is_dir() for c in cases)


def test_unknown_check_kind_is_a_case_error_naming_the_file(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text(
        "id: bad\nprompt: hi\nworkspace: shop\napproval: ask\n"
        "checks:\n  - kind: looks_good\n")
    with pytest.raises(CaseError, match="bad.yaml"):
        load_cases(tmp_path)


def test_unknown_key_and_missing_workspace_are_case_errors(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("id: a\nprompt: hi\nworkspace: shop\napproval: ask\n"
                                     "colour: blue\n")
    with pytest.raises(CaseError, match="colour"):
        load_cases(tmp_path)
    (tmp_path / "a.yaml").write_text("id: a\nprompt: hi\nworkspace: nowhere\napproval: ask\n")
    with pytest.raises(CaseError, match="nowhere"):
        load_cases(tmp_path)


def _case(*checks: Check) -> Case:
    return Case(id="c", prompt="p", workspace=FIXTURES_DIR / "shop", approval="ask", flags={},
                allow_commands=(), checks=checks, expected={}, max_seconds=60.0)


def test_file_contains_and_edits_only_in(tmp_path: Path) -> None:
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text("TAX_RATE = 0.2\n")
    diffs = [DiffRecord("shop/pricing.py", "+TAX_RATE = 0.2", True),
             DiffRecord("README.md", "+x", False)]
    results = run_checks(_case(
        Check(kind="file_contains", path="shop/pricing.py", contains="TAX"),
        Check(kind="file_exists", path="CONTRIBUTING.md"),
        Check(kind="edits_only_in", paths=("shop/",))), tmp_path, [], {}, "", diffs)
    assert [r.passed for r in results] == [True, False, True]
    stray = run_checks(_case(Check(kind="edits_only_in", paths=())), tmp_path, [], {}, "",
                       diffs)
    assert stray[0].passed is False and "shop/pricing.py" in stray[0].detail


def test_answer_event_tokens_and_failure_checks(tmp_path: Path) -> None:
    case = _case(Check(kind="answer_mentions", phrases=("Pricing", "cart")),
                 Check(kind="event_fired", event="memory_compacted"),
                 Check(kind="input_tokens_below", limit=1000),
                 Check(kind="no_failure_message"))
    results = run_checks(case, tmp_path, [{"type": "memory_compacted"}],
                         {"total": {"input": 900}}, "pricing reads the cart", [])
    assert [r.passed for r in results] == [True, True, True, True]
    failed = run_checks(case, tmp_path, [], {"total": {"input": 5000}},
                        "⚠️ The turn failed and had to stop: boom", [])
    assert [r.passed for r in failed] == [False, False, False, False]


def test_command_passes_runs_after_the_episode_in_the_trial_ws(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURES_DIR / "shop", ws)
    check = Check(kind="command_passes", command="python -m pytest -q -p no:cacheprovider")
    (before,) = run_checks(_case(check), ws, [], {}, "", [])
    assert before.passed is False and "1 failed" in before.detail
    pricing = ws / "shop" / "pricing.py"
    pricing.write_text(pricing.read_text().replace(
        "return amount * DISCOUNTS[code]", "return amount * (1 - DISCOUNTS[code])"))
    (after,) = run_checks(_case(check), ws, [], {}, "", [])
    assert after.passed is True


def test_fixture_has_exactly_one_failing_test(tmp_path: Path) -> None:
    shutil.copytree(FIXTURES_DIR / "shop", tmp_path / "shop")
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                         cwd=tmp_path / "shop", capture_output=True, text=True, timeout=120)
    assert "1 failed, 3 passed" in out.stdout


def test_cases_dir_is_beside_the_fixtures() -> None:
    assert CASES_DIR.parent == FIXTURES_DIR.parent


def test_command_check_never_sees_secret_env(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    # The command runs code the agent may have edited: it must not see provider keys.
    monkeypatch.setenv("OPENAI_API_KEY", "probe-not-a-real-key")
    probe = ("python -c \"import os,sys; "
             "sys.exit(1 if os.environ.get('OPENAI_API_KEY') else 0)\"")
    (result,) = run_checks(_case(Check(kind="command_passes", command=probe)), tmp_path,
                           [], {}, "", [])
    assert result.passed is True, result.detail
