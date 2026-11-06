"""Online evaluation: judge a sample of real finished requests in the background and attach
the verdicts to their traces (observability spec §6.3). Off by default; needs tracing on.
Best-effort like the memory consolidator: nothing here can fail or slow a turn."""
from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from agentd.chat.storage import ChatThreadStore
from agentd.eval.trajectory.annotations import (
    annotations_for,
    phoenix_base_url,
    post_span_annotations,
)
from agentd.eval.trajectory.episode import build_episode
from agentd.eval.trajectory.judge import judge_episode
from agentd.providers.usage import METER
from agentd.telemetry import tracer

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE = 0.1
# Sampling decisions remembered per request; older ones are long settled.
_DECISION_LIMIT = 4096


@dataclass(frozen=True)
class OnlineConfig:
    enabled: bool
    sample: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OnlineConfig:
        source = os.environ if env is None else env
        enabled = (source.get("CRUCIBLE_EVAL_ONLINE") or "").strip().lower() in {
            "1", "true", "yes", "on"}
        try:
            sample = float(source.get("CRUCIBLE_EVAL_ONLINE_SAMPLE") or DEFAULT_SAMPLE)
        except ValueError:
            sample = DEFAULT_SAMPLE
        return cls(enabled=enabled, sample=min(1.0, max(0.0, sample)))


# Returns the judge's transport and model at call time, so a provider hot-swap applies.
JudgeSource = Callable[[], tuple[Any, str]]


class OnlineEvaluator:
    def __init__(self, *, store: ChatThreadStore, workspace_path: Path, judge: JudgeSource,
                 phoenix_endpoint: str, sample: float,
                 rng: Callable[[], float] = random.random,
                 http_client: httpx.AsyncClient | None = None) -> None:
        self._store = store
        self._workspace = workspace_path
        self._judge = judge
        self._base_url = phoenix_base_url(phoenix_endpoint)
        self._sample = sample
        self._rng = rng
        self._client = http_client
        self._decisions: OrderedDict[str, bool] = OrderedDict()
        self._tasks: set[asyncio.Task[None]] = set()
        self._warned = False

    def maybe_judge(self, thread_id: str, request_id: str | None, *, settled: bool,
                    access_stopped: bool) -> None:
        """Called at every turn end, with no await. Sampling is decided once per request;
        a sampled request is judged when its episode settles, once."""
        if request_id is None:
            return
        if not tracer.is_active():
            if not self._warned:
                self._warned = True
                logger.warning("CRUCIBLE_EVAL_ONLINE is set but tracing is off; online "
                               "evaluation needs CRUCIBLE_TRACING_ENABLED")
            return
        decision = self._decisions.get(request_id)
        if decision is None:
            decision = self._rng() < self._sample
            self._decisions[request_id] = decision
            while len(self._decisions) > _DECISION_LIMIT:
                self._decisions.popitem(last=False)
        if not decision:
            return
        if access_stopped:
            # A provider stop says nothing about the agent; never judged, now or later.
            self._decisions[request_id] = False
            return
        if not settled:
            return
        self._decisions[request_id] = False  # judged once
        task = asyncio.get_running_loop().create_task(self._judge_one(thread_id, request_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _judge_one(self, thread_id: str, request_id: str) -> None:
        run_id = f"online:{thread_id}"
        try:
            episode = build_episode(self._store, self._workspace, thread_id,
                                    request_id=request_id)
            span_id = episode.turns[0].span_id if episode.turns else None
            if span_id is None:
                return
            transport, model = self._judge()
            result = await judge_episode(transport, model, episode, run_id=run_id)
            # The turn's spans must reach Phoenix before an annotation can name them.
            await asyncio.to_thread(tracer.flush)
            await post_span_annotations(self._base_url, annotations_for(result, span_id),
                                        client=self._client)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort, never reaches a turn
            logger.warning("[eval] online judging failed (thread=%s)", thread_id,
                           exc_info=True)
        finally:
            METER.take(f"eval:{run_id}")

    async def drain(self) -> None:
        """Wait for judgments in flight (tests, and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
