"""Mutable current-provider holder. main.py constructs one per process with every
live DefaultReasoningEngine (orchestrator's + the chat controller's); the hot-swap
route calls swap(), which validates first and only then mutates — a failed swap
leaves everything untouched. Known v1 limitation: the memory-harness summarizer
keeps its construction-time transport until restart."""
from __future__ import annotations

from collections.abc import Sequence

from agentd.providers.factory import build_transport, resolve_model
from agentd.providers.validate import ProviderValidationError, ping_transport


class ProviderRuntime:
    def __init__(
        self,
        *,
        backend: str,
        model: str,
        engines: Sequence[object],
        window_sinks: Sequence[object] = (),
        context_window: int | None = None,
        config_error: str | None = None,
    ) -> None:
        self.backend = backend
        self.model = model
        # Why the startup transport could not be built, when it could not. The
        # backend still runs (see providers/unconfigured.py); this is what lets
        # GET /v1/config tell the UI why nothing works. A successful swap clears
        # it, because a swap builds a real transport — that IS the recovery.
        self.config_error = config_error
        # The window in effect right now. Seeded in main.py from MemoryConfig — i.e.
        # from CRUCIBLE_MEMORY_WINDOW_TOKENS or its 128000 default — so GET /v1/config
        # reports the real number from the first request, before anyone has saved
        # anything in the settings panel. That seeding IS the spec's resolution order:
        # provider window > env var > default, collapsed into one value.
        self.context_window = context_window
        self._engines = list(engines)
        # Duck-typed on set_window_tokens rather than typed as MemoryHarness: the
        # providers package should not grow a dependency on the memory package for
        # one method call, and the same reasoning already governs `engines`.
        self._window_sinks = list(window_sinks)

    async def swap(
        self,
        *,
        backend: str,
        model: str | None = None,
        credentials: dict[str, str] | None = None,
        context_window: int | None = None,
    ) -> dict[str, object]:
        try:
            transport = build_transport(backend, credentials=credentials)
            resolved = model or resolve_model(backend)
        except Exception as exc:
            # Identical reasoning to ping_provider's, and the identical error type
            # so the route needs no new handler: a transport that refuses to
            # construct ("CRUCIBLE_OPENAI_COMPAT_BASE_URL is required…") is a user
            # configuration mistake carrying its own actionable message. Raised as
            # a bare RuntimeError it escaped the route's handler entirely and came
            # back as a 500 with nothing the UI could show.
            raise ProviderValidationError(str(exc)) from exc
        await ping_transport(transport, resolved)  # raises ProviderValidationError
        for engine in self._engines:
            engine.set_provider(model=resolved, transport=transport)  # type: ignore[attr-defined]
        self.backend, self.model = backend, resolved
        # A real transport now exists, so whatever failed at startup no longer
        # describes this process.
        self.config_error = None
        # Applied only after validation succeeds, for the same reason the engines
        # are: a rejected swap must leave the process exactly as it was. Absent
        # means "unchanged", not "reset" — a model-only hot-swap from the composer
        # must not discard the window the user declared in Settings.
        if context_window is not None:
            self.context_window = context_window
            for sink in self._window_sinks:
                sink.set_window_tokens(context_window)  # type: ignore[attr-defined]
        result: dict[str, object] = {"backend": backend, "model": resolved}
        if self.context_window is not None:
            result["context_window"] = self.context_window
        return result
