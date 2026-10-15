import pytest

from agentd.memory.recall_context import CharacterCodec, context_pairs, previous_closing
from agentd.memory.reranker import Reranker


def test_closing_uses_last_substantive_completed_answer():
    messages = [
        {"role": "agent", "content": "backend unavailable", "metadata": {"system_notice": True}},
        {"role": "agent", "content": "⚠️ Usage limit reached. Try again later."},
        {"role": "agent", "content": "partial", "metadata": {"inflight_turn_id": "turn"}},
        {"role": "agent", "content": "reading", "metadata": {"progress": True}},
        {"role": "agent", "content": "changed mode", "metadata": {"breadcrumb": True}},
        {"role": "agent", "type": "task", "content": "task result"},
        {"role": "user", "content": "request"},
        {"role": "agent", "content": "⚠️ Keep reduced-motion support in the design."},
    ]
    assert previous_closing(messages) == "⚠️ Keep reduced-motion support in the design."
    assert previous_closing(messages[:-1]) == ""


def test_combined_order_and_missing_signals():
    codec = CharacterCodec()
    pairs, result = context_pairs(
        {"Q": "edit", "C": "answer", "R": "recap"}, ["claim\ndetail"], codec,
    )
    assert pairs == [("Thread recap:\nrecap\n\nPrevious substantive answer:\nanswer"
                      "\n\nCurrent request:\nedit", "claim\ndetail")]
    assert result.truncated_signals == result.truncated_documents == []
    pairs, _ = context_pairs({"Q": "edit", "C": "", "R": " "}, ["claim"], codec)
    assert pairs == [("Current request:\nedit", "claim")]


def test_long_unicode_inputs_fit_pair_budget_and_preserve_both_ends():
    codec = CharacterCodec()
    signals = {key: "START " + "日🙂 text " * 500 + " END" for key in "QCR"}
    document = "CLAIM " + "body " * 500 + " FINAL"
    pairs, result = context_pairs(signals, [document, "short"], codec)
    assert set(result.truncated_signals) == set("QCR")
    assert result.truncated_documents == [0]
    assert result.max_pair_tokens <= 512
    assert "CLAIM" in pairs[0][1] and "FINAL" in pairs[0][1]
    assert result.query.count("START") == result.query.count("END") == 3
    for query, body in pairs:
        assert len(codec.encode(query)) + len(codec.encode(body)) + 4 <= 512


@pytest.mark.parametrize("bad_scores", [[float("nan")], [], [0.1, 0.2]])
def test_combined_scorer_rejects_invalid_results(bad_scores):
    ranker = Reranker(scorer=lambda pairs: bad_scores)
    with pytest.raises(ValueError, match="invalid scores"):
        ranker.score_context({"Q": "request"}, ["memory"])
    assert not ranker.available
