"""Score frozen curator results and validate writes only in a temporary store.

This heuristic cannot replace independent human labels or a real-store duplicate
study. It never writes the live store and never uploads evidence or results.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from agentd.chat.storage import ChatThreadStore
from agentd.memory.curation_input import assemble_chunks
from agentd.memory.curator import Curator
from agentd.memory.durable_store import DurableMemoryStore
from agentd.memory.recall_v2 import CandidateSearch
from agentd.memory.scope import ScopeContext
from agentd.memory.store import MemoryStore


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def indexed(items: list[dict[str, Any]], count: int) -> None:
    if sorted(item["index"] for item in items) != list(range(count)):
        raise ValueError("Judge must cover every index exactly once")


async def validate_new(
    chunk: dict[str, Any], output: dict[str, Any], root: Path,
) -> dict[str, Any]:
    """Use the actual proposal validator and audited batch writer, without local models."""
    durable = DurableMemoryStore(root / "durable.sqlite3")
    evidence = MemoryStore(root / "evidence.sqlite3")
    threads = ChatThreadStore(root / "threads.sqlite3")

    async def unused(_payload: dict[str, object]) -> object:
        raise AssertionError("Validation must not call a provider")

    curator = Curator(evidence, durable, threads, CandidateSearch(durable), unused,
                      repo_id="evaluation", workspace_path=str(root), active=lambda _: False)
    context = ScopeContext("evaluation", chunk["thread"])
    decisions: list[dict[str, Any]] = []
    calls = output["production_calls"]
    blocks = assemble_chunks(chunk["raw_sources"])
    if len(calls) != len(blocks):
        raise ValueError("Production renderer changed after extraction")
    try:
        index = 0
        for call_index, (call, block) in enumerate(zip(calls, blocks, strict=True)):
            if call["input_sha256"] != hashlib.sha256(block.text.encode()).hexdigest():
                raise ValueError("Production input changed after extraction")
            for proposal in call["response"]["memories"]:
                prepared, dropped = await curator._prepare([proposal], block, [], context)
                counts = durable.curated_batch(f"{call_index}:{index}", prepared, context)
                decisions.append({"index": index, "accepted": counts["saved"] == 1,
                                  "dropped": dropped + counts["dropped"]})
                index += 1
        return {"entries": decisions, "saved": len(durable.list_memories(context)),
                "audit_rows": durable._conn.execute(
                    "SELECT count(*) FROM memory_audit",
                ).fetchone()[0],
                "semantic_dedup": "Not tested: no local models; exact dedup exercised",
                "store": "Fresh temporary copy, not the populated production store"}
    finally:
        durable.close()
        evidence._conn.close()
        threads._conn.close()


def aggregate(report: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    for split in ("development", "held_out", "all"):
        group = [case for case in report["chunks"].values()
                 if split == "all" or case["split"] == split]
        for variant in ("old", "new"):
            entries = [(entry, case) for case in group for entry in case["entries"]
                       if case["variants"][entry["index"]] == variant
                       and (variant == "old" or entry["index"] in case["accepted_indices"])]
            keep = sum(entry["keep"] for entry, _ in entries)
            gold = sum(len(case["gold"]) for case in group)
            matched = sum(len({index for entry, parent in entries
                               if parent is case and entry["keep"]
                               for index in entry["gold_indices"]}) for case in group)
            counts[f"{split}:{variant}"] = {
                "chunks": len(group), "written": len(entries), "keep": keep,
                "precision": keep / len(entries) if entries else None,
                "status": sum(entry["status"] for entry, _ in entries),
                "unsupported_causes": sum(entry["unsupported_cause"] for entry, _ in entries),
                "gold_total": gold, "gold_found": matched,
                "recall": matched / gold if gold else None,
            }
    return counts


async def run(args: argparse.Namespace) -> None:
    payload = json.loads(args.payload.read_text())
    gold_path, extraction_path = args.results / "gold.json", args.results / "extraction.json"
    gold, extraction = json.loads(gold_path.read_text()), json.loads(extraction_path.read_text())
    payload_hash = digest(args.payload)
    if any(record["payload_sha256"] != payload_hash for record in (gold, extraction)):
        raise ValueError("Frozen payload mismatch")
    if extraction["gold_sha256"] != digest(gold_path):
        raise ValueError("Gold changed since extraction")
    prelabels = json.loads(args.prelabels.read_text())["labels"]
    report: dict[str, Any] = json.loads(args.output.read_text()) if args.output.exists() else {
        "payload_sha256": payload_hash, "gold_sha256": digest(gold_path),
        "extraction_sha256": digest(extraction_path), "chunks": {}, "calls": [],
        "limitations": ["Model gold and Codex review are provisional, not Claude blind agreement.",
                         "Codex prelabels cover five stripped chunks and disagree with model gold.",
                         "Held-out chunks share threads with development; no prompt tuning here.",
                         "Offline mseq and excluded legacy evictions limit source coverage.",
                         "Old prompt is an ablation on the new input, not full legacy pipeline.",
                         "Fresh stores test exact duplicates within spans, not a populated store.",
                         "Gold quote validation drops are reported in gold.json, not repaired."],
    }
    if report["extraction_sha256"] != digest(extraction_path):
        raise ValueError("Extraction changed; use a fresh score file")
    review = json.loads(args.review.read_text())
    report["review_sha256"] = digest(args.review)
    report["reviewer"] = "Codex, local post-output review; not independent blind gold"
    for chunk in payload["chunks"]:
        key = chunk["id"]
        if key in report["chunks"]:
            continue
        result = extraction["chunks"][key]
        if any(variant not in result for variant in ("old", "new")):
            raise ValueError("Complete both extractors before scoring")
        with tempfile.TemporaryDirectory(prefix="curator-validate-") as temporary:
            validation = await validate_new(chunk, result["new"], Path(temporary))
        lessons = [dict(item, label_origin="model")
                   for item in gold["chunks"][key]["raw"]["lessons"]]
        # Preserve separate label origins; no post-output adjudication silently
        # changes the frozen reference set.
        lessons += [dict(item, label_origin="Codex prelabel") for item in prelabels.get(key, [])]
        entries: list[dict[str, Any]] = []
        variants: list[str] = []
        accepted: list[int] = []
        for variant in ("old", "new"):
            for index, item in enumerate(result[variant]["memories"]):
                if variant == "new" and validation["entries"][index]["accepted"]:
                    accepted.append(len(entries))
                entries.append({"index": len(entries), "proposal": item})
                variants.append(variant)
        response = review["chunks"][key]
        indexed(response["entries"], len(entries))
        for entry in response["entries"]:
            if any(type(i) is not int or not 0 <= i < len(lessons)
                   for i in entry["gold_indices"]):
                raise ValueError("Invalid gold index")
        report["chunks"][key] = {
            **response, "split": chunk["split"], "gold": lessons,
            "variants": variants, "accepted_indices": accepted, "validation": validation,
        }
        print(key, "validated and reviewed locally", flush=True)
        report["metrics"] = aggregate(report)
        args.output.write_text(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--prelabels", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
