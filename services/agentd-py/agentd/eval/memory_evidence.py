"""Offline evidence-size and fold-budget checks, using SQLite strictly read-only.

No model calls or raw session text are exported. This is not a summary-quality evaluation.
Run with python -m agentd.eval.memory_evidence --help.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from agentd.memory.evidence import evidence_text, evidence_turns, render_evidence
from agentd.memory.folding import EvidenceBudgetExceeded, next_fold
from agentd.memory.harness import summary_input_tokens
from agentd.memory.models import CompactionSegment

Source = Mapping[str, object] | CompactionSegment
_DECLARATION = re.compile(
    r"^\s*(?:\d+:\s*)?(?:export\s+)?(?:async\s+)?"
    r"(?:def|class|function|interface|type|struct|fn)\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)


def _content(source: Source) -> str:
    if isinstance(source, CompactionSegment):
        return source.content
    return str(source.get("content", ""))


def _number(entry: Mapping[str, object], key: str) -> int:
    value = entry[key]
    assert isinstance(value, int)
    return value


def measure(sources: Sequence[Source]) -> dict[str, object]:
    rows = render_evidence(sources, include_thoughts=True)
    raw_chars = sum(len(_content(source)) + 8 for source in sources)  # legacy role separators
    rendered = evidence_text(rows)
    file_names = 0
    retained_names = 0
    for source in sources:
        tool = source.tool if isinstance(source, CompactionSegment) else source.get("tool")
        if tool == "read_file":
            names = set(_DECLARATION.findall(_content(source)))
            file_names += len(names)
            # This is a literal-name retention diagnostic, not factual entailment or quality.
            retained_names += sum(name in rendered for name in names)
    return {
        "source_rows": len(sources),
        "rendered_rows": len(rows),
        "raw_chars_approx": raw_chars,
        "rendered_chars": len(rendered),
        "reduction_percent": round((1 - len(rendered)/raw_chars)*100, 2) if raw_chars else 0,
        "categories": dict(Counter(row.category for row in rows)),
        "source_ids_available": sum(row.mseq is not None for row in rows),
        "observed_declaration_names": file_names,
        "declaration_names_present_anywhere_in_rendering": retained_names,
    }


def budget_replay(sources: Sequence[Source], window: int) -> dict[str, object]:
    turns = evidence_turns(render_evidence(sources, include_thoughts=True))
    capacity = min(500_000, max(int(window * .65), window - 28_000))
    sizes: list[int] = []
    consumed_total = 0
    anchor = ""
    blocked = False
    while turns:
        try:
            text, consumed = next_fold(
                turns, anchor, summary_input_tokens, target=min(200_000, capacity), hard=capacity,
            )
        except EvidenceBudgetExceeded:
            blocked = True
            break
        sizes.append(summary_input_tokens(anchor, text))
        consumed_total += consumed
        # A fixed mock anchor isolates input-budget mechanics. No LLM coverage is inferred.
        anchor = "Mechanical replay anchor; no model summary was generated."
        turns = turns[consumed:]
    return {
        "window": window, "calls": len(sizes), "max_estimated_input_tokens": max(sizes, default=0),
        "turns_consumed": consumed_total, "blocked_indivisible_input": blocked,
    }


def _read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def evaluate(chat_path: Path, segments_path: Path) -> dict[str, object]:
    groups: list[tuple[str, list[Source]]] = []
    with closing(_read_only(chat_path)) as chat:
        snapshots = chat.execute(
            "SELECT thread_id,controller_history_json FROM chat_threads ORDER BY thread_id",
        ).fetchall()
    digest = hashlib.sha256()
    for row in snapshots:
        digest.update(row["thread_id"].encode())
        digest.update((row["controller_history_json"] or "[]").encode())
        groups.append(("history:" + row["thread_id"], json.loads(
            row["controller_history_json"] or "[]",
        )))
    segment_groups: dict[str, list[Source]] = defaultdict(list)
    with closing(_read_only(segments_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(compaction_segments)")}
        raw_segments = conn.execute(
            "SELECT * FROM compaction_segments ORDER BY run_id,seq",
        ).fetchall()
    segment_digest = hashlib.sha256()
    for row in raw_segments:
        segment_digest.update(json.dumps(dict(row), sort_keys=True).encode())
        segment = CompactionSegment(
            id=row["id"], run_id=row["run_id"], seq=row["seq"], content=row["content"],
            created_at=datetime.fromisoformat(row["created_at"]),
            role=row["role"] if "role" in columns else None,
            tool=row["tool"] if "tool" in columns else None,
            mseq=row["mseq"] if "mseq" in columns else None,
            metadata=json.loads(row["metadata"] or "{}") if "metadata" in columns else {},
        )
        segment_groups["segments:" + row["run_id"] + ":" + row["created_at"]].append(segment)
    groups.extend(segment_groups.items())
    summaries = []
    for identity, sources in groups:
        summaries.append({
            "case_id": identity, **measure(sources),
            "budgets": [budget_replay(sources, window) for window in (128_000, 272_000, 1_000_000)],
        })
    totals = {}
    for kind in ("history:", "segments:"):
        selected = [entry for entry in summaries if str(entry["case_id"]).startswith(kind)]
        raw = sum(_number(entry, "raw_chars_approx") for entry in selected)
        rendered = sum(_number(entry, "rendered_chars") for entry in selected)
        totals[kind[:-1]] = {
            "cases": len(selected),
            "source_rows": sum(_number(entry, "source_rows") for entry in selected),
            "raw_chars_approx": raw, "rendered_chars": rendered,
            "reduction_percent": round((1-rendered/raw)*100, 2) if raw else 0,
        }
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "model_calls": 0,
        "chat_source_sha256": digest.hexdigest(),
        "segment_source_sha256": segment_digest.hexdigest(),
        "queries": [
            "SELECT thread_id,controller_history_json FROM chat_threads ORDER BY thread_id",
            "SELECT * FROM compaction_segments ORDER BY run_id,seq",
        ],
        "limitations": [
            "Mock anchors validate budgets, not LLM summary coverage or successful resume.",
            "Legacy segments lacking role/tool are rendered as unknown, not reconstructed.",
            "Literal declaration retention is a diagnostic, not a reference-fact score.",
            "Token counts are conservative character estimates, not measured provider usage.",
        ],
        "totals": totals, "cases": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-db", type=Path, required=True)
    parser.add_argument("--segments-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.chat_db, args.segments_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"totals": report["totals"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
