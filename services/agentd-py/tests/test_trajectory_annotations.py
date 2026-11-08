"""Judge scores posted to Phoenix as span annotations (observability spec §6.3)."""
from __future__ import annotations

import json

import httpx
import pytest

from agentd.eval.trajectory.annotations import (
    annotations_for,
    phoenix_base_url,
    post_span_annotations,
)
from agentd.eval.trajectory.judge import (
    DIMENSIONS,
    JUDGE_VERSION,
    DimensionVerdict,
    JudgeResult,
)

RESULT = JudgeResult(
    verdicts={d: DimensionVerdict("fail" if d == "stayed_in_scope" else "pass", f"{d} reason")
              for d in DIMENSIONS},
    error=None, version=JUDGE_VERSION, usage_owner="eval:r1")


def test_one_annotation_per_dimension_with_a_stable_identifier() -> None:
    notes = annotations_for(RESULT, "abcd1234abcd1234")
    assert [n.name for n in notes] == [f"judge.{d}" for d in DIMENSIONS]
    scope = notes[-1]
    assert (scope.label, scope.score, scope.explanation) == (
        "fail", 0.0, "stayed_in_scope reason")
    assert scope.identifier == f"{JUDGE_VERSION}:stayed_in_scope"
    assert scope.span_id == "abcd1234abcd1234"


def test_a_judge_error_gives_no_annotations() -> None:
    failed = JudgeResult(None, "boom", JUDGE_VERSION, "eval:r1")
    assert annotations_for(failed, "abcd1234abcd1234") == []


def test_phoenix_base_url_strips_the_traces_path() -> None:
    assert phoenix_base_url("http://localhost:6006/v1/traces") == "http://localhost:6006"
    assert phoenix_base_url("http://localhost:6006/") == "http://localhost:6006"


@pytest.mark.asyncio
async def test_annotation_payload_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "1"}, {"id": "2"}, {"id": "3"},
                                                  {"id": "4"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        posted = await post_span_annotations(
            "http://localhost:6006", annotations_for(RESULT, "abcd1234abcd1234"),
            client=client)
    assert posted == 4
    (request,) = seen
    assert request.url.path == "/v1/span_annotations"
    assert request.url.params["sync"] == "true"
    body = json.loads(request.content)
    first = body["data"][0]
    assert set(first) == {"span_id", "name", "annotator_kind", "result", "identifier"}
    assert first["annotator_kind"] == "LLM"
    assert first["result"] == {"label": "pass", "score": 1.0,
                               "explanation": "task_completed reason"}


@pytest.mark.asyncio
async def test_phoenix_errors_are_logged_never_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Span not found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await post_span_annotations(
            "http://localhost:6006", annotations_for(RESULT, "ab" * 8), client=client) == 0

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refused)) as client:
        assert await post_span_annotations(
            "http://localhost:6006", annotations_for(RESULT, "ab" * 8), client=client) == 0


@pytest.mark.asyncio
async def test_nothing_to_post_makes_no_request() -> None:
    assert await post_span_annotations("http://localhost:1", []) == 0


@pytest.mark.asyncio
async def test_a_non_json_reply_counts_as_nothing_posted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>proxy page</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await post_span_annotations(
            "http://localhost:6006", annotations_for(RESULT, "ab" * 8), client=client) == 0
