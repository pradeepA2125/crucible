from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class ModelJsonTransport(Protocol):
    # Whether this transport's grammar engine enforces a JSON-schema `oneOf` at the
    # token level (lets the controller use a tight discriminated-union schema instead
    # of the flat fallback). Default False — only a transport that has MEASURED its
    # engine enforces `oneOf` (e.g. TurboQuant/llama.cpp) overrides this. Gemini
    # deadlocks on `oneOf`, so it stays False. Read defensively via getattr so a
    # transport predating this attribute is treated as non-supporting.
    supports_oneof_grammar: bool = False

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
    ) -> dict[str, object]: ...

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
    ) -> str: ...
