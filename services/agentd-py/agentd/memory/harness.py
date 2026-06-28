from __future__ import annotations

import logging

from agentd.memory.compactor import AnchorSummarizer, Compactor
from agentd.memory.config import MemoryConfig
from agentd.memory.models import History, TurnPreparation
from agentd.memory.store import MemoryStore
from agentd.providers.contracts import ModelJsonTransport

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = (
    "You maintain a running summary of an AI coding session. Merge the prior summary and the "
    "newly evicted messages into one updated summary. Preserve goals, decisions, file/symbol "
    "names, and unresolved threads. Do not drop facts from the prior summary. Be concise but "
    "lossless on decisions and identifiers. Return only the updated summary."
)


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
            history=result.history, recalled_memories=[], compacted=result.compacted
        )

    async def recall(self, query: str, run_id: str) -> History:
        return []  # Phase 2


NO_OP_HARNESS = MemoryHarness(enabled=False, compactor=None)


def make_engine_summarizer(transport: ModelJsonTransport, model: str) -> AnchorSummarizer:
    async def _summarize(old_anchor: str, evicted_text: str) -> str:
        return await transport.generate_text(
            model=model,
            system_instructions=_SUMMARY_SYSTEM,
            user_payload={
                "prior_summary": old_anchor or "(none)",
                "evicted_messages": evicted_text,
            },
        )

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
