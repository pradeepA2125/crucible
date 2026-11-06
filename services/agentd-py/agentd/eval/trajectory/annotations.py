"""Judge scores as Phoenix span annotations, posted to Phoenix's REST API (observability spec
§6.3). Plain httpx: no Phoenix client is pinned inside agentd."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from agentd.eval.trajectory.judge import JudgeResult

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class SpanAnnotation:
    span_id: str
    name: str
    label: str
    score: float
    explanation: str
    # Re-posting the same judge version's verdict updates it instead of adding another.
    identifier: str


def annotations_for(result: JudgeResult, span_id: str) -> list[SpanAnnotation]:
    if result.verdicts is None:
        return []
    return [SpanAnnotation(span_id=span_id, name=f"judge.{dimension}", label=v.verdict,
                           score=1.0 if v.verdict == "pass" else 0.0, explanation=v.reason,
                           identifier=f"{result.version}:{dimension}")
            for dimension, v in result.verdicts.items()]


def phoenix_base_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base.removesuffix("/v1/traces")


async def post_span_annotations(base_url: str, annotations: Sequence[SpanAnnotation], *,
                                client: httpx.AsyncClient | None = None) -> int:
    """Post the annotations; returns how many Phoenix accepted. Never raises: Phoenix being
    down, or a span it hasn't received, is a WARNING and 0."""
    if not annotations:
        return 0
    body = {"data": [{"span_id": a.span_id, "name": a.name, "annotator_kind": "LLM",
                      "result": {"label": a.label, "score": a.score,
                                 "explanation": a.explanation},
                      "identifier": a.identifier} for a in annotations]}
    url = f"{base_url.rstrip('/')}/v1/span_annotations"
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as own:
                response = await own.post(url, params={"sync": "true"}, json=body)
        else:
            response = await client.post(url, params={"sync": "true"}, json=body)
    except httpx.HTTPError as exc:
        logger.warning("[eval] could not post annotations to %s: %s", url, exc)
        return 0
    if response.status_code != 200:
        logger.warning("[eval] Phoenix refused annotations (%s): %s", response.status_code,
                       response.text[:300])
        return 0
    try:
        data = response.json().get("data")
    except (ValueError, AttributeError):
        logger.warning("[eval] Phoenix answered annotations with a non-JSON body: %s",
                       response.text[:200])
        return 0
    return len(data) if isinstance(data, list) else 0
