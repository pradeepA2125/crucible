"""Sampled online judging of finished requests (observability spec §6.3)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentd.chat.models import ChatMessage
from agentd.chat.storage import ChatThreadStore
from agentd.eval.trajectory.judge import DIMENSIONS
from agentd.eval.trajectory.online import OnlineConfig, OnlineEvaluator
from agentd.telemetry import tracer
from tests.test_telemetry_turn_spans import _controller, _scripted
from tests.test_trajectory_episode import _trace

ANSWER: dict[str, object] = {"type": "answer", "thought": "t", "answer": "done"}


class _Judge:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def generate_json(self, **_kwargs: Any) -> dict[str, object]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {d: {"verdict": "pass", "reason": "ok"} for d in DIMENSIONS}


@pytest.fixture
def traced() -> Iterator[Any]:
    exporter = tracer.install_test_exporter()
    yield exporter
    tracer.reset_for_tests()


def _world(tmp_path: Path) -> tuple[ChatThreadStore, str, str]:
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    tid = store.create_thread(str(tmp_path), "t").thread_id
    request = str(store.append_message(tid, ChatMessage(
        role="user", content="hi", timestamp=datetime.now(UTC))))
    _trace(tmp_path, tid, "u1", kind="user", started=datetime.now(UTC), request_id=request,
           text="done")
    return store, tid, request


def _evaluator(tmp_path: Path, store: ChatThreadStore, judge: _Judge, rolls: list[float],
               posts: list[httpx.Request]) -> OnlineEvaluator:
    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(200, json={"data": [{"id": str(i)} for i in range(4)]})

    return OnlineEvaluator(
        store=store, workspace_path=tmp_path, judge=lambda: (judge, "j"),
        phoenix_endpoint="http://localhost:6006/v1/traces", sample=0.1,
        rng=lambda: rolls.pop(0),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_config_defaults_off_with_a_tenth_sampled() -> None:
    assert OnlineConfig.from_env({}) == OnlineConfig(enabled=False, sample=0.1)
    assert OnlineConfig.from_env({"CRUCIBLE_EVAL_ONLINE": "1",
                                  "CRUCIBLE_EVAL_ONLINE_SAMPLE": "0.5"}).sample == 0.5
    assert OnlineConfig.from_env({"CRUCIBLE_EVAL_ONLINE_SAMPLE": "7"}).sample == 1.0
    assert OnlineConfig.from_env({"CRUCIBLE_EVAL_ONLINE_SAMPLE": "lots"}).sample == 0.1


@pytest.mark.asyncio
async def test_sampled_settled_episode_is_judged_once_and_annotated(
    tmp_path: Path, traced: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    store, tid, request = _world(tmp_path)
    judge, posts = _Judge(), []
    ev = _evaluator(tmp_path, store, judge, [0.0], posts)
    ev.maybe_judge(tid, request, settled=True, access_stopped=False)
    ev.maybe_judge(tid, request, settled=True, access_stopped=False)
    await ev.drain()
    assert judge.calls == 1
    (post,) = posts
    body = json.loads(post.content)
    assert [a["name"] for a in body["data"]] == [f"judge.{d}" for d in DIMENSIONS]
    assert {a["span_id"] for a in body["data"]} == {"s" * 16}


@pytest.mark.asyncio
async def test_unsampled_episode_is_never_judged_even_after_more_turns(
    tmp_path: Path, traced: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    store, tid, request = _world(tmp_path)
    judge, posts = _Judge(), []
    ev = _evaluator(tmp_path, store, judge, [0.99, 0.0], posts)
    ev.maybe_judge(tid, request, settled=False, access_stopped=False)
    ev.maybe_judge(tid, request, settled=True, access_stopped=False)
    await ev.drain()
    assert judge.calls == 0 and posts == []


@pytest.mark.asyncio
async def test_unsettled_episode_waits_for_the_turn_that_settles_it(
    tmp_path: Path, traced: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    store, tid, request = _world(tmp_path)
    judge, posts = _Judge(), []
    ev = _evaluator(tmp_path, store, judge, [0.0], posts)
    ev.maybe_judge(tid, request, settled=False, access_stopped=False)
    await ev.drain()
    assert judge.calls == 0
    ev.maybe_judge(tid, request, settled=True, access_stopped=False)
    await ev.drain()
    assert judge.calls == 1


@pytest.mark.asyncio
async def test_provider_access_stop_is_not_judged(
    tmp_path: Path, traced: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    store, tid, request = _world(tmp_path)
    judge, posts = _Judge(), []
    ev = _evaluator(tmp_path, store, judge, [0.0, 0.0], posts)
    ev.maybe_judge(tid, request, settled=True, access_stopped=True)
    ev.maybe_judge(tid, request, settled=True, access_stopped=False)
    await ev.drain()
    assert judge.calls == 0


@pytest.mark.asyncio
async def test_disabled_without_tracing_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    tracer.reset_for_tests()
    store, tid, request = _world(tmp_path)
    judge, posts = _Judge(), []
    ev = _evaluator(tmp_path, store, judge, [0.0, 0.0], posts)
    with caplog.at_level(logging.WARNING, logger="agentd.eval.trajectory.online"):
        ev.maybe_judge(tid, request, settled=True, access_stopped=False)
        ev.maybe_judge(tid, request, settled=True, access_stopped=False)
    await ev.drain()
    assert judge.calls == 0
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


@pytest.mark.asyncio
async def test_judge_failure_never_reaches_the_turn(
    tmp_path: Path, traced: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    store, tid, request = _world(tmp_path)
    judge, posts = _Judge(error=RuntimeError("judge down")), []
    ev = _evaluator(tmp_path, store, judge, [0.0], posts)
    ev.maybe_judge(tid, request, settled=True, access_stopped=False)
    await ev.drain()
    assert judge.calls == 1 and posts == []


@pytest.mark.asyncio
async def test_controller_turn_end_hands_the_episode_to_the_evaluator(
    tmp_path: Path, traced: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, store, tid = _controller(tmp_path, _scripted(ANSWER))
    seen: list[tuple[str, str | None, bool, bool]] = []

    class _Recorder:
        def maybe_judge(self, thread_id: str, request_id: str | None, *, settled: bool,
                        access_stopped: bool) -> None:
            seen.append((thread_id, request_id, settled, access_stopped))

    ctrl.attach_online_evaluator(_Recorder())  # type: ignore[arg-type]
    task = ctrl.launch_turn(tid, ctrl.handle_message(tid, "hi", channel_id="c1"),
                            channel_id="c1")
    await task
    await asyncio.sleep(0)
    assert seen and seen[-1][0] == tid and seen[-1][2] is True and seen[-1][3] is False
    assert seen[-1][1] == ctrl.latest_user_message_id(tid)
