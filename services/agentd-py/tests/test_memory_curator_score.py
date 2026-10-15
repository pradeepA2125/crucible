import hashlib
import json
from argparse import Namespace

import pytest

from agentd.eval.memory_curator_quality import run
from agentd.eval.memory_curator_score import aggregate, indexed, validate_new
from agentd.memory.curation_input import assemble_chunks
from agentd.providers.plan_access import PlanUnsupportedCapability


def test_metrics_exclude_rejected_writes_and_keep_splits_separate():
    report = {"chunks": {"a": {
        "split": "development", "gold": ["lesson"],
        "variants": ["old", "new", "new"], "accepted_indices": [2],
        "entries": [
            {"index": 0, "keep": False, "status": True,
             "unsupported_cause": False, "gold_indices": []},
            {"index": 1, "keep": True, "status": False,
             "unsupported_cause": False, "gold_indices": [0]},
            {"index": 2, "keep": False, "status": False,
             "unsupported_cause": True, "gold_indices": []},
        ],
    }}}
    scores = aggregate(report)
    assert scores["all:new"]["written"] == 1 and scores["all:new"]["recall"] == 0
    assert scores["all:new"]["unsupported_causes"] == 1
    assert scores["all:old"]["status"] == 1
    assert scores["held_out:new"]["written"] == 0


def test_review_cannot_skip_or_duplicate_output_indices():
    with pytest.raises(ValueError):
        indexed([{"index": 0}, {"index": 0}], 2)
    with pytest.raises(ValueError):
        indexed([], 1)


@pytest.mark.asyncio
async def test_validator_uses_exact_input_and_audits_only_accepted_entries(tmp_path):
    source = {"mseq": 1, "role": "user", "content": "Use native image parts, never prompt text."}
    chunk = {"raw_sources": [source], "thread": "offline"}
    block = assemble_chunks([source])[0]
    proposal = {"kind": "procedural", "content": "Use native image parts.",
                "topic": "images", "applies_when": "Sending images", "detail": "",
                "entities": [], "source_message_id": 1, "source_quote": "Use native image parts",
                "operation": "save", "target_id": None}
    output = {"production_calls": [{
        "input_sha256": hashlib.sha256(block.text.encode()).hexdigest(),
        "response": {"memories": [proposal, {**proposal, "source_quote": "fabricated quote"}]},
    }]}
    before = json.dumps(chunk)
    result = await validate_new(chunk, output, tmp_path)
    assert result["saved"] == result["audit_rows"] == 1
    assert [item["accepted"] for item in result["entries"]] == [True, False]
    assert json.dumps(chunk) == before
    output["production_calls"][0]["input_sha256"] = "changed"
    with pytest.raises(ValueError, match="input changed"):
        await validate_new(chunk, output, tmp_path / "different")


@pytest.mark.asyncio
async def test_extraction_stops_after_first_account_access_refusal(tmp_path, monkeypatch):
    calls = []

    class Refused:
        def set_reasoning_effort(self, _effort):
            pass

        async def generate_json(self, **kwargs):
            calls.append(kwargs)
            raise PlanUnsupportedCapability("fictional model is unsupported", status=400)

    monkeypatch.setattr("agentd.eval.memory_curator_quality.build_transport",
                        lambda *args, **kwargs: Refused())
    chunks = [{"id": key, "title": "fixture", "split": "development",
               "raw_sources": [{"mseq": 1, "role": "user", "content": "Use native image parts"}]}
              for key in ("a", "b")]
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"chunks": chunks}))
    (tmp_path / "gold.json").write_text(json.dumps({
        "payload_sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        "chunks": {key: {"raw": {}, "stripped": {}} for key in ("a", "b")},
    }))
    await run(Namespace(payload=payload, output=tmp_path, model="fictional", registration="test",
                        split="all", stage="extract"))
    report = json.loads((tmp_path / "extraction.json").read_text())
    assert len(calls) == 1
    assert report["stopped"]["kind"] == "unsupported_capability"
    assert "b" not in report["chunks"]
