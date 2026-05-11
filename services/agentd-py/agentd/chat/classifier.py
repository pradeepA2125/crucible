from __future__ import annotations

import logging
from typing import Any

from agentd.chat.models import IntentClassification, IntentType

logger = logging.getLogger(__name__)

_CLASSIFY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["qa", "small_change", "large_change"]},
        "rationale": {"type": "string"},
        "likely_targets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "rationale", "likely_targets"],
}

_SYSTEM_PROMPT = """\
You are classifying a user's chat message to decide the execution path:
  qa           — question or discussion, no file changes needed
  small_change — 1-2 files, localised edit, no interface or schema changes
  large_change — 3+ files, interface/schema changes, new files, or ambiguous scope

You receive:
  conversation_history — recent messages; use to resolve "fix that", "also update tests", etc.
  explore_context      — files already read and search results gathered from the workspace

Count distinct files in explore_context to judge scope. Be conservative — prefer large_change
when scope is unclear.
"""


class IntentClassifier:
    def __init__(self, *, transport: Any, model: str) -> None:
        self._transport = transport
        self._model = model

    async def classify(
        self,
        message: str,
        context: list[dict[str, Any]],
        history: list[dict[str, str]],
    ) -> IntentClassification:
        if message.strip().startswith("/plan"):
            return IntentClassification(
                intent=IntentType.LARGE_CHANGE,
                rationale="/plan prefix — forced large_change routing",
            )
        try:
            result = await self._transport.generate_json(
                model=self._model,
                schema_name="intent_classification",
                schema=_CLASSIFY_SCHEMA,
                system_instructions=_SYSTEM_PROMPT,
                user_payload={
                    "message": message,
                    "conversation_history": history[-10:],
                    "explore_context": context,
                },
            )
            return IntentClassification(
                intent=IntentType(result["intent"]),
                rationale=result.get("rationale", ""),
                likely_targets=result.get("likely_targets", []),
            )
        except Exception:
            logger.exception("Intent classification failed — defaulting to large_change")
            return IntentClassification(
                intent=IntentType.LARGE_CHANGE,
                rationale="classification error — safe default",
            )
