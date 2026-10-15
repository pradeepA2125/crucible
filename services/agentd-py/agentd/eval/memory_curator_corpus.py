"""Prepare local, read-only curator review inputs; never call a provider.

Legacy histories may be explicitly stamped with offline-only sequence IDs. That
mode is diagnostic and cannot establish production source-pointer correctness or
complete coverage of untyped legacy evictions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from agentd.history_metadata import message_sequence
from agentd.memory.curation_input import CurationChunk, assemble_chunks, collect_sources
from agentd.memory.models import CompactionSegment


def read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def prepare(chat_path: Path, memory_path: Path, *, stamp_legacy: bool) -> dict[str, object]:
    chat, memory = read_only(chat_path), read_only(memory_path)
    try:
        histories = chat.execute(
            "SELECT thread_id,title,controller_history_json FROM chat_threads "
            "WHERE controller_history_json IS NOT NULL ORDER BY thread_id",
        ).fetchall()
        all_segments = memory.execute("SELECT * FROM compaction_segments ORDER BY run_id,seq")
        segments: dict[str, list[CompactionSegment]] = {}
        for row in all_segments:
            data = dict(row)
            segments.setdefault(row["run_id"], []).append(CompactionSegment(
                id=data["id"], run_id=data["run_id"], seq=data["seq"], content=data["content"],
                created_at=datetime.fromisoformat(data["created_at"]), mseq=data.get("mseq"),
                role=data.get("role"), tool=data.get("tool"),
                metadata=json.loads(data.get("metadata_json") or "{}"),
            ))
        candidates: list[tuple[
            sqlite3.Row, list[CurationChunk], bool,
            list[CompactionSegment], list[dict[str, object]],
        ]] = []
        skipped = 0
        for row in histories:
            history = json.loads(row["controller_history_json"])
            legacy = any(type(message.get("mseq")) is not int for message in history)
            if legacy and not stamp_legacy:
                skipped += 1
                continue
            if legacy:
                # Deterministic offline source-order IDs, not production ID
                # allocation. Never migrate a live history/database.
                history = [{**message, "mseq": i + 1} for i, message in enumerate(history)]
            evicted = segments.get(row["thread_id"], [])
            sources = collect_sources(evicted, history, 0)
            chunks = assemble_chunks(sources)
            if chunks:
                candidates.append((row, chunks, legacy, evicted, sources))
        compacted = [item for item in candidates if item[3]]
        never = [item for item in candidates if not item[3]]
        # Five threads in each stratum, largest first to retain multi-chunk coverage.
        chosen = sorted(compacted, key=lambda item: (-len(item[1]), str(item[0]["thread_id"])))[:5]
        chosen += sorted(never, key=lambda item: (-len(item[1]), str(item[0]["thread_id"])))[:5]
        selected = [(item, 0) for item in chosen]
        for item in chosen:
            for index in range(1, len(item[1])):
                if len(selected) < 15:
                    selected.append((item, index))
        payloads = []
        for index, (item, chunk_index) in enumerate(selected):
            row, chunks, legacy, evicted, sources = item
            chunk = chunks[chunk_index]
            low = chunks[chunk_index - 1].through_mseq + 1 if chunk_index else 1
            raw = json.dumps([source for source in sources
                              if (seq := message_sequence(source)) is not None
                              and low <= seq <= chunk.through_mseq],
                             sort_keys=True)
            payloads.append({
                "id": f"{row['thread_id']}:{chunk_index}", "thread": row["thread_id"],
                "split": "held_out" if index >= 10 else "development",
                "title": row["title"], "context_recap": "unverified: no historical recap generated",
                "compacted": bool(evicted), "offline_source_ids": legacy,
                "excluded_untyped_evictions": sum(segment.mseq is None for segment in evicted),
                "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "through_mseq": chunk.through_mseq, "raw_sources": json.loads(raw),
                "stripped": chunk.text, "omitted_source_ids": chunk.omitted,
                "gold_lessons": None, "review_status": "unlabelled; no model output reviewed",
            })
        return {
            "version": 1, "upload_authorized": False, "model_calls": 0,
            "queries": ["PRAGMA query_only=ON",
                        "SELECT thread_id,title,controller_history_json FROM chat_threads "
                        "WHERE controller_history_json IS NOT NULL ORDER BY thread_id",
                        "SELECT * FROM compaction_segments ORDER BY run_id,seq"],
            "skipped_unsequenced_histories": skipped, "chunks": payloads,
            "thread_count": len({item["thread"] for item in payloads}),
            "limitations": ["Gold and independent labels must precede extraction runs.",
                            "Offline source IDs are not production pointers.",
                            "Excluded legacy evictions prevent a complete thread-coverage claim.",
                            "This local payload differs from the 20 approved redacted samples."],
        }
    finally:
        chat.close()
        memory.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chat", type=Path)
    parser.add_argument("memory", type=Path)
    parser.add_argument("--stamp-legacy-for-evaluation", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.chat, args.memory, stamp_legacy=args.stamp_legacy_for_evaluation)
    args.output.write_text(json.dumps(result, indent=2))
    print("Prepared", len(result["chunks"]), "chunks", result["thread_count"], "threads")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
