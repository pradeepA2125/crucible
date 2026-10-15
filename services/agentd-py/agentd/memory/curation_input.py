"""Turn-bounded curator input assembled from original, sequenced evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agentd.history_metadata import message_sequence
from agentd.memory.evidence import EvidenceRow, capped, evidence_text, render_evidence
from agentd.memory.models import CompactionSegment
from agentd.subagents.framing import strip_frames

# Leave room in the 24k-token call for system/schema, 20 catalog lines and JSON
# escaping. The complete assembled request is measured again before every call.
EVIDENCE_CHARS = 48_000
_MEMORY_TOOLS = {"read_memory", "search_memory", "remember", "forget", "recall"}


@dataclass(frozen=True)
class CurationChunk:
    rows: tuple[EvidenceRow, ...]
    sources: Mapping[int, Mapping[str, object]]
    through_mseq: int
    omitted: tuple[int, ...] = ()

    @property
    def text(self) -> str:
        text = evidence_text(self.rows)
        if self.omitted:
            text += "\n[… omitted source messages: " + ",".join(map(str, self.omitted)) + " …]"
        return text

    @property
    def query(self) -> str:
        # User request text, never a status/importance ordering of the old corpus.
        return "\n".join(str(message.get("content", "")) for message in self.sources.values()
                         if message.get("role") == "user")[-4000:]


def _source(segment: CompactionSegment) -> dict[str, object]:
    return {**segment.metadata, "mseq": segment.mseq, "role": segment.role or "unknown",
            "tool": segment.tool or "", "content": segment.content}


def collect_sources(
    segments: Sequence[CompactionSegment], hot: Sequence[Mapping[str, object]], watermark: int,
) -> list[dict[str, object]]:
    found: dict[int, dict[str, object]] = {}
    # Raw evictions are authoritative over a surviving shortened hot message.
    for message in [_source(segment) for segment in segments]:
        seq = message_sequence(message)
        if seq is not None and seq > watermark and (
            seq not in found or len(str(message.get("content", "")))
            > len(str(found[seq].get("content", "")))
        ):
            found[seq] = message
    for original in hot:
        message = dict(original)
        seq = message_sequence(message)
        if seq is not None and seq > watermark:
            found.setdefault(seq, message)
    return [found[seq] for seq in sorted(found)]


def _sanitize(original: Mapping[str, object]) -> dict[str, object]:
    message = dict(original)
    content = str(message.get("content", ""))
    message["content"] = strip_frames(content)
    if message.get("role") == "user" and "<<<agent-content " in content:
        # A system-generated notice has role=user but is not a user assertion.
        message["_curator_notice"] = True
    if message.get("role") == "assistant":
        try:
            action = json.loads(content)
        except ValueError:
            action = None
        if isinstance(action, dict):
            # Frames embedded in JSON strings contain escaped newlines. Strip
            # the decoded fields, not only the serialized action envelope.
            def strip_nested(value: object) -> object:
                if isinstance(value, str):
                    return strip_frames(value)
                if isinstance(value, list):
                    return [strip_nested(item) for item in value]
                if isinstance(value, dict):
                    return {key: strip_nested(item) for key, item in value.items()}
                return value

            action = strip_nested(action)
            message["content"] = json.dumps(action, ensure_ascii=False)
    private = message.get("_evidence")
    if isinstance(private, dict):
        private = dict(private)
        if isinstance(private.get("thought"), str):
            private["thought"] = strip_frames(private["thought"])
        message["_evidence"] = private
    return message


def curation_rows(
    sources: Sequence[Mapping[str, object]], *, capped_outputs: bool = True,
) -> list[EvidenceRow]:
    """Shared exclusions for production input and an uncapped evaluation control."""
    sanitized = [_sanitize(message) for message in sources]
    rows = render_evidence(sanitized, include_thoughts=True, purpose="curation",
                           capped_outputs=capped_outputs)
    return [row for row in rows if row.category != "instruction"
            and not _memory_echo(sanitized[row.source_index])
            and not sanitized[row.source_index].get("_curator_notice")]


def _memory_echo(message: Mapping[str, object]) -> bool:
    if message.get("tool") in _MEMORY_TOOLS:
        return True
    if message.get("role") != "assistant":
        return False
    try:
        action = json.loads(str(message.get("content", "")))
    except ValueError:
        return False
    if not isinstance(action, dict):
        return False
    calls = action.get("calls", [action])
    return isinstance(calls, list) and bool(calls) and all(
        isinstance(call, dict) and call.get("tool") in _MEMORY_TOOLS for call in calls
    )


def _bounded_turn(rows: list[EvidenceRow], limit: int) -> tuple[list[EvidenceRow], list[int]]:
    if len(json.dumps(evidence_text(rows))) <= limit:
        return rows, []
    chosen: set[int] = set()
    used = 0
    # Newest in each tier first. Account for both separators and omission markers.
    budget = max(0, limit - 300 - len(rows) * 14)
    for i in sorted(range(len(rows)), key=lambda i: (rows[i].priority, -i)):
        size = len(json.dumps(rows[i].text)) + 4
        if used + size <= budget:
            chosen.add(i)
            used += size
    omitted = [row.mseq for i, row in enumerate(rows) if i not in chosen and row.mseq is not None]
    return [row for i, row in enumerate(rows) if i in chosen], omitted


def assemble_chunks(
    sources: Sequence[Mapping[str, object]], *, evidence_chars: int = EVIDENCE_CHARS,
) -> list[CurationChunk]:
    if evidence_chars < 1000:
        raise ValueError("Curator input budget is too small")
    sanitized = [_sanitize(message) for message in sources]
    by_seq = {seq: message for message in sanitized
              if (seq := message_sequence(message)) is not None}
    rendered = {row.mseq: row for row in curation_rows(sanitized) if row.mseq is not None}
    # A user request and all subsequent agent actions/results form an indivisible
    # conversation turn. Large turns use priority omission, never raw truncation.
    turns: list[list[Mapping[str, object]]] = []
    for message in sanitized:
        if message.get("role") == "user" and not message.get("_curator_notice") or not turns:
            turns.append([])
        turns[-1].append(message)
    chunks: list[CurationChunk] = []
    rows: list[EvidenceRow] = []
    omitted: list[int] = []
    through = 0

    def flush() -> None:
        nonlocal rows, omitted, through
        if through:
            allowed = {row.mseq: by_seq[row.mseq] for row in rows if row.mseq is not None}
            chunks.append(CurationChunk(tuple(rows), allowed, through, tuple(omitted)))
        rows, omitted, through = [], [], 0

    for turn in turns:
        seqs = [seq for message in turn if (seq := message_sequence(dict(message))) is not None]
        if not seqs:
            continue
        turn_rows = [rendered[seq] for message in turn
                     if (seq := message_sequence(dict(message))) in rendered
                     and not _memory_echo(message) and not message.get("_curator_notice")]
        bounded, missing = _bounded_turn(turn_rows, evidence_chars)
        if (rows and len(json.dumps(evidence_text([*rows, *bounded])))
                + len(omitted + missing) * 14 > evidence_chars):
            flush()
        rows.extend(bounded)
        omitted.extend(missing)
        through = max(seqs)
    flush()
    return chunks


def chunk_context(title: str, recap: str) -> dict[str, str]:
    return {"title": capped(title, 200), "recap": capped(recap, 400)}
