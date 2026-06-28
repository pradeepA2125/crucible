from __future__ import annotations

import json
import logging
import re

from agentd.memory.compactor import AnchorSummarizer, Compactor
from agentd.memory.config import MemoryConfig
from agentd.memory.models import History, TurnPreparation
from agentd.memory.store import MemoryStore
from agentd.providers.contracts import ModelJsonTransport

logger = logging.getLogger(__name__)

# Running-memory prompt. Modelled on the Claude Code conversation-summarization template
# (sectioned, distill-don't-copy), tightened against two failure modes seen live on a
# weaker local model: (1) the model echoed its JSON input payload back verbatim, (2) the
# anchor ballooned because raw file/tool output was carried in. The <summary> delimiter lets
# us extract the answer and detect echoes. Carry-forward is goal-relevant, not strictly
# lossless: a fully-superseded file may be dropped to keep the note bounded (recency-triage
# is accepted by design — a durable file-ledger outside the LLM summary is the deferred fix).
_SUMMARY_SYSTEM = (
    "You are the assistant in an ongoing AI coding session, writing a note to your future "
    "self. You will be given your PREVIOUS memory note and the NEW messages that are about "
    "to scroll out of view. The raw messages will be discarded and replaced by your note, so "
    "the note must carry everything you need to keep working as if you had read it all.\n"
    "\n"
    "Write the note under these headings. Skip any heading that has nothing to record:\n"
    "1. Goal: what the user is ultimately trying to achieve, in their own words.\n"
    "2. Key concepts: the frameworks, patterns, and conventions in play.\n"
    "3. Files and code: every file, function, class, or symbol read or changed — give the "
    "exact name and a one-line note on what it is for. Keep a short code snippet only when a "
    "later step depends on its exact shape. Never paste whole files.\n"
    "4. Errors and fixes: problems hit and how they were resolved, including failures still "
    "open.\n"
    "5. Decisions: approaches chosen and why, trade-offs weighed, and options ruled out.\n"
    "6. User instructions: the user's explicit requests and feedback, kept close to their "
    "wording.\n"
    "7. Open threads: unfinished work, known bugs, and questions still to answer.\n"
    "8. Current work: what was happening immediately before this note was written.\n"
    "9. Next step: the single action that follows directly from the user's latest request.\n"
    "\n"
    "Rules:\n"
    "- Carry forward the facts, decisions, and identifiers from the PREVIOUS memory note that "
    "are still relevant to the current goal. A file or detail fully superseded by later work "
    "may be dropped to keep the note focused.\n"
    "- Keep identifiers exact: file paths, function names, and error text verbatim. Summarize "
    "everything else and prefer the shortest wording that keeps the fact.\n"
    "- Do not copy raw messages, tool output, or file contents — keep only what is needed to "
    "continue.\n"
    "- Write plain prose under the headings. Do not output JSON, key/value pairs, or the "
    "input you were given, and do not repeat these instructions.\n"
    "- Put the entire note inside one <summary>...</summary> block and write nothing outside "
    "it."
)

_SUMMARY_RE = re.compile(r"<summary>(.*)</summary>", re.DOTALL | re.IGNORECASE)


class SummarizerEchoError(RuntimeError):
    """The summarizer returned its input payload (or empty/JSON) instead of a real summary."""


def _extract_summary(raw: str) -> str:
    """Pull the text inside the <summary>...</summary> block; fall back to the stripped whole."""
    match = _SUMMARY_RE.search(raw)
    return (match.group(1) if match else raw).strip()


def _is_echo(text: str) -> bool:
    """True when the candidate is empty or a JSON object — both signal a failed summary.

    A genuine prose summary never parses as a JSON object; the live failure was the model
    parroting its `{"prior_summary": ..., "evicted_messages": ...}` payload back verbatim.
    """
    stripped = text.strip()
    if not stripped:
        return True
    try:
        return isinstance(json.loads(stripped), dict)
    except (ValueError, TypeError):
        return False


def _render_transcript(old_anchor: str, evicted_text: str) -> str:
    """One plain-text field — a JSON-shaped multi-key payload is what the model echoed."""
    prior = old_anchor.strip() or "(none yet — this is the first memory note)"
    return f"PREVIOUS MEMORY NOTE:\n{prior}\n\nNEW MESSAGES TO FOLD IN:\n{evicted_text}"


class MemoryHarness:
    """The only memory unit the loops see. Compaction in Phase 1; recall is a Phase-2 stub."""

    def __init__(self, *, enabled: bool, compactor: Compactor | None) -> None:
        self._enabled = enabled
        self._compactor = compactor

    async def prepare_turn(self, history: History, run_id: str) -> TurnPreparation:
        if not self._enabled or self._compactor is None:
            return TurnPreparation(history=history, recalled_memories=[], compacted=False)
        try:
            result = await self._compactor.maybe_compact(history, run_id)
        except Exception:  # best-effort: memory must never break a loop iteration
            logger.warning("[memory] prepare_turn failed for run=%s", run_id, exc_info=True)
            return TurnPreparation(history=history, recalled_memories=[], compacted=False)
        return TurnPreparation(
            history=result.history,
            recalled_memories=[],
            compacted=result.compacted,
            evicted_count=result.evicted_count,
            anchor_version=result.anchor_version,
        )

    async def recall(self, query: str, run_id: str) -> History:
        return []  # Phase 2


NO_OP_HARNESS = MemoryHarness(enabled=False, compactor=None)


def make_engine_summarizer(transport: ModelJsonTransport, model: str) -> AnchorSummarizer:
    async def _summarize(old_anchor: str, evicted_text: str) -> str:
        payload: dict[str, object] = {"transcript": _render_transcript(old_anchor, evicted_text)}
        # One retry: a weaker model intermittently echoes its input instead of summarizing.
        # On the second echo we raise so the Compactor degrades (keeps the prior anchor)
        # rather than persisting garbage as the new anchor.
        for attempt in range(2):
            raw = await transport.generate_text(
                model=model, system_instructions=_SUMMARY_SYSTEM, user_payload=payload
            )
            summary = _extract_summary(raw)
            if not _is_echo(summary):
                return summary
            logger.warning(
                "[memory] summarizer echoed input (attempt %d/2) for model=%s", attempt + 1, model
            )
        raise SummarizerEchoError("summarizer returned no usable summary after retry")

    return _summarize


def build_memory_harness(
    config: MemoryConfig, transport: ModelJsonTransport, model: str
) -> MemoryHarness:
    if not config.enabled:
        return NO_OP_HARNESS
    store = MemoryStore(config.db_path)
    compactor = Compactor(
        store,
        make_engine_summarizer(transport, model),
        window_tokens=config.window_tokens,
        trigger_frac=config.trigger_frac,
        hot_token_frac=config.hot_token_frac,
        hot_turns=config.hot_turns,
    )
    return MemoryHarness(enabled=True, compactor=compactor)
