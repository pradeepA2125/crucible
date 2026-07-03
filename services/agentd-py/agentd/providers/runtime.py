"""Mutable current-provider holder. main.py constructs one per process with every
live DefaultReasoningEngine (orchestrator's + the chat controller's); the hot-swap
route calls swap(), which validates first and only then mutates — a failed swap
leaves everything untouched. Known v1 limitation: the memory-harness summarizer
keeps its construction-time transport until restart."""
from __future__ import annotations

from collections.abc import Sequence

from agentd.providers.factory import build_transport, resolve_model
from agentd.providers.validate import ping_transport


class ProviderRuntime:
    def __init__(self, *, backend: str, model: str, engines: Sequence[object]) -> None:
        self.backend = backend
        self.model = model
        self._engines = list(engines)

    async def swap(
        self,
        *,
        backend: str,
        model: str | None = None,
        credentials: dict[str, str] | None = None,
    ) -> dict[str, str]:
        transport = build_transport(backend, credentials=credentials)
        resolved = model or resolve_model(backend)
        await ping_transport(transport, resolved)  # raises ProviderValidationError
        for engine in self._engines:
            engine.set_provider(model=resolved, transport=transport)  # type: ignore[attr-defined]
        self.backend, self.model = backend, resolved
        return {"backend": backend, "model": resolved}
