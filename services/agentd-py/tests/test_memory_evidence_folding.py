import json

import pytest

from agentd.memory.compactor import Compactor, estimate_tokens
from agentd.memory.config import MemoryConfig
from agentd.memory.evidence import evidence_turns, render_evidence
from agentd.memory.folding import EvidenceBudgetExceeded, next_fold
from agentd.memory.harness import (
    _SUMMARY_SYSTEM,
    _render_transcript,
    build_memory_harness,
    summary_input_tokens,
)
from agentd.memory.store import MemoryStore


def count(old, text):
    return len(old) + len(text) + 10


def test_turns_respect_anchor_budget_and_indivisible_oversize():
    turns = ["first", "second", "third"]
    assert next_fold(turns, "anchor", count, target=30, hard=40) == ("first\n\nsecond", 2)
    assert next_fold(["x" * 25], "", count, target=30, hard=40) == ("x" * 25, 1)
    with pytest.raises(EvidenceBudgetExceeded):
        next_fold(["x" * 31], "", count, target=30, hard=40)
    with pytest.raises(EvidenceBudgetExceeded):
        next_fold(["x"], "a" * 40, count, target=30, hard=40)


def test_estimate_includes_json_escaping_prior_prompt_and_transport():
    old, text = "old\\\n" * 100, 'new"\n你好' * 100
    payload = json.dumps({"transcript": _render_transcript(old, text)}, indent=2)
    assert summary_input_tokens(old, text) == estimate_tokens(_SUMMARY_SYSTEM + payload) + 2049
    assert summary_input_tokens(old, text) > estimate_tokens(old + text)


def histories():
    # One logical action per pair, with a result that must not get separated.
    history = []
    for i in range(5):
        history.extend([
            {"role": "assistant", "content": f"action {i} " + "x" * 80, "mseq": 2*i + 1},
            {"role": "tool_result", "tool": "run_command", "content": f"RESULT {i}",
             "mseq": 2*i + 2},
        ])
    history.append({"role": "user", "content": "newest request", "mseq": 11})
    return history


def compactor(store, summarize):
    return Compactor(
        store, summarize, window_tokens=350, reserve_tokens=100, hot_turns=1,
        compact_evidence=True, summary_input_tokens=count,
    )


@pytest.mark.asyncio
async def test_fold_commits_in_order_and_preserves_raw_sources(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    calls = []

    async def summarize(old, text):
        calls.append((old, text))
        assert count(old, text) <= 250
        for i in range(5):
            assert (f"action {i}" in text) == (f"RESULT {i}" in text)
        return f"anchor {len(calls)}"

    history = histories()
    outcome = await compactor(store, summarize).maybe_compact(history, "r", force=True)
    assert len(calls) == 5
    assert [old for old, text in calls] == ["", "anchor 1", "anchor 2", "anchor 3", "anchor 4"]
    assert outcome.anchor == "anchor 5" and outcome.anchor_version == 5
    assert not outcome.degraded and outcome.history[-1] == history[-1]
    assert [s.content for s in store.get_segments("r")] == [m["content"] for m in history[:-1]]


@pytest.mark.asyncio
async def test_later_failure_keeps_last_committed_anchor(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    calls = []

    async def summarize(old, text):
        calls.append((old, text))
        if len(calls) == 2:
            raise RuntimeError("provider down")
        return "last good"

    outcome = await compactor(store, summarize).maybe_compact(histories(), "r", force=True)
    assert len(calls) == 2 and outcome.degraded
    assert outcome.anchor == store.get_anchor("r").summary_md == "last good"
    assert outcome.anchor_version == 1 and len(store.get_segments("r")) == 10
    # Only the successfully summarized prefix may disappear from model context.
    assert outcome.history[1:] == histories()[2:]

    retry_calls = []

    async def recovered(old, text):
        retry_calls.append(text)
        return "recovered anchor"

    retry = await compactor(store, recovered).maybe_compact(outcome.history, "r", force=True)
    assert not retry.degraded and retry.anchor == "recovered anchor"
    assert all("action 0" not in text for text in retry_calls)
    assert "action 4" in "\n".join(retry_calls)


@pytest.mark.asyncio
async def test_oversized_indivisible_batch_never_reaches_provider(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.upsert_anchor("r", "prior")

    async def never(old, text):
        raise AssertionError("oversized input reached provider")

    history = [
        {"role": "assistant", "content": "action"},
        *[{"role": "tool_result", "tool": "run_command", "content": "output"*100}
          for _ in range(3)],
        {"role": "user", "content": "next"},
    ]
    outcome = await compactor(store, never).maybe_compact(history, "r", force=True)
    assert outcome.degraded and outcome.anchor == "prior"
    assert len(store.get_segments("r")) == 4
    assert store.get_anchor("r").version == 1
    assert outcome.history[1:] == history


@pytest.mark.asyncio
async def test_excluded_only_round_avoids_model_call_and_repeat_summary(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.upsert_anchor("r", "prior")
    calls = []

    async def never(old, text):
        calls.append(text)
        return "incorrect"

    history = [
        {"role": "user", "content":
            "[MEMORY] Summary of earlier conversation that was compacted:\nprior"},
        {"role": "tool_result", "tool": "recall", "content": "recalled body"},
        {"role": "user", "content": "next"},
    ]
    outcome = await compactor(store, never).maybe_compact(history, "r", force=True)
    assert calls == [] and outcome.anchor == "prior" and outcome.anchor_version == 1


@pytest.mark.asyncio
async def test_output_too_large_is_failed_fold_not_persisted(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.upsert_anchor("r", "prior")

    async def huge(old, text):
        return "x" * 300

    outcome = await compactor(store, huge).maybe_compact(histories(), "r", force=True)
    assert outcome.degraded and outcome.anchor == "prior"
    assert store.get_anchor("r").version == 1


@pytest.mark.asyncio
async def test_window_change_while_folding_rechecks_next_call_budget(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    calls = []
    comp = None

    async def summarize(old, text):
        calls.append(text)
        comp.cap_window_tokens(100)
        return "good"

    comp = compactor(store, summarize)
    outcome = await comp.maybe_compact(histories(), "r", force=True)
    assert len(calls) == 1 and outcome.degraded and outcome.anchor == "good"
    assert store.get_anchor("r").summary_md == "good"


@pytest.mark.asyncio
async def test_image_tombstone_survives_summary_that_drops_it(tmp_path):
    store = MemoryStore(tmp_path / "m.db")

    async def summarize(old, text):
        return "summary omitted image"

    history = [
        {"role": "user", "content": "image question", "image_refs": [{
            "asset_id": "a" * 64, "name": "x.png", "media_type": "image/png",
            "width": 100, "height": 100,
        }]},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "next"},
    ]
    comp = Compactor(
        store, summarize, window_tokens=10000, reserve_tokens=1000, hot_turns=1,
        compact_evidence=True, summary_input_tokens=count,
    )
    outcome = await comp.maybe_compact(history, "r", force=True)
    assert not outcome.degraded
    assert "[Image " in outcome.anchor and "unavailable:" in outcome.anchor
    assert "x.png" in store.get_anchor("r").summary_md
    assert store.get_segments("r")[0].metadata["image_refs"] == history[0]["image_refs"]


def test_renderer_gate_is_independent_of_recall_and_does_not_create_global_store(
    tmp_path, monkeypatch,
):
    import agentd.memory.harness as module

    monkeypatch.setattr(module.threading.Thread, "start", lambda self: None)
    config = MemoryConfig.from_env({
        "CRUCIBLE_MEMORY_COMPACT_EVIDENCE": "1",
        "CRUCIBLE_MEMORY_DB_PATH": str(tmp_path / "workspace.db"),
        "CRUCIBLE_MEMORY_STORE_PATH": str(tmp_path / "global.db"),
    })
    harness = build_memory_harness(config, object(), "fake", workspace_path=str(tmp_path))
    assert harness._compactor._compact_evidence
    assert not harness.recall_v2 and not (tmp_path / "global.db").exists()
    assert not MemoryConfig.from_env({}).compact_evidence


def test_large_window_uses_two_targeted_calls_and_absolute_hard_limit():
    turns = evidence_turns(render_evidence([
        {"role": "user", "content": "x" * 8000} for _ in range(100)
    ]))
    first, n = next_fold(
        turns, "", summary_input_tokens, target=200000, hard=500000,
    )
    assert 0 < n < len(turns) and summary_input_tokens("", first) <= 200000
    second, remaining = next_fold(
        turns[n:], "prior summary", summary_input_tokens, target=200000, hard=500000,
    )
    assert remaining == len(turns) - n and summary_input_tokens("prior summary", second) <= 200000
    with pytest.raises(EvidenceBudgetExceeded):
        next_fold(["x" * 1_600_000], "", summary_input_tokens, target=900000, hard=900000)


@pytest.mark.asyncio
async def test_factory_summarizer_receives_capped_evidence_and_full_prompt_budget(tmp_path):
    captured = []

    class Transport:
        async def generate_text(self, *, model, system_instructions, user_payload):
            captured.append((system_instructions, user_payload))
            return "<summary>## Goal\nGood anchor</summary>"

    cfg = MemoryConfig.from_env({
        "CRUCIBLE_MEMORY_COMPACT_EVIDENCE": "1",
        "CRUCIBLE_MEMORY_DB_PATH": str(tmp_path / "workspace.db"),
    })
    harness = build_memory_harness(cfg, Transport(), "fake")
    original = "HEAD" + "MIDDLE" * 5000 + "TAIL"
    history = [
        {"role": "assistant", "content": json.dumps({
            "type": "tool_call", "tool": "read_file", "args": {"path": "file.py"},
        })},
        {"role": "tool_result", "tool": "read_file", "content": original},
        *[{"role": "user", "content": "hot turn"} for _ in range(12)],
    ]
    prep = await harness.prepare_turn(history, "r", force=True)
    assert prep.compacted and len(captured) == 1
    system, payload = captured[0]
    assert system == _SUMMARY_SYSTEM and "file.py" in payload["transcript"]
    assert "chars omitted" in payload["transcript"] and "TAIL" in payload["transcript"]
    assert estimate_tokens(system + json.dumps(payload, indent=2)) + 2049 <= 100000
    assert harness._store.get_segments("r")[1].content == original
