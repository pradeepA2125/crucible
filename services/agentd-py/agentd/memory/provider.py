"""Mutable provider binding shared by foreground and background memory callbacks."""

from __future__ import annotations

from typing import Any


class MemoryProvider:
    supports_vision = False
    supports_oneof_grammar = False

    def __init__(self, transport: object, model: str) -> None:
        self.transport, self.model = transport, model

    def set_provider(self, *, transport: object, model: str) -> None:
        self.transport, self.model = transport, model

    async def generate_json(self, *, model: str = "", **kwargs: Any) -> dict[str, object]:
        # Resolve once per call. A validated swap affects subsequent calls, not a
        # response already in flight. Nonempty model is an explicit curator override.
        transport, resolved = self.transport, model or self.model
        return await transport.generate_json(model=resolved, **kwargs)  # type: ignore[attr-defined,no-any-return]

    async def generate_text(self, *, model: str = "", **kwargs: Any) -> str:
        transport, resolved = self.transport, model or self.model
        return await transport.generate_text(model=resolved, **kwargs)  # type: ignore[attr-defined,no-any-return]
