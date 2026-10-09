"""Mutable current-provider holder. main.py constructs one per process with every
live DefaultReasoningEngine (orchestrator's + the chat controller's); the hot-swap
route calls swap(), which validates first and only then mutates — a failed swap
leaves everything untouched. Known v1 limitation: the memory-harness summarizer
keeps its construction-time transport until restart."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from agentd.chatgpt_auth.service import ChatGPTAuthService, chatgpt_auth_service
from agentd.chatgpt_auth.session import ChatGPTPlanBearer
from agentd.providers.factory import build_transport, resolve_model
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort
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
        transport: object | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        auth_service: ChatGPTAuthService | None = None,
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
        # Capability is a property of the transport, and swap() previously threw
        # away the one it built. Held here so GET /v1/config can answer "which
        # rungs does the CURRENT provider actually expose".
        self._transport = transport
        # The effective (post-clamp) rung, and why it differs from the request when
        # it does. None means "send nothing" — the pre-feature default, which is
        # also what leaves each transport's own env-configured behavior in place.
        self.reasoning_effort = reasoning_effort
        self.reasoning_effort_note: str | None = None
        self._auth_service = auth_service
        self._context_listeners: list[Callable[[], None]] = []
        self._auth_unsubscribe: Callable[[], None] | None = None
        self._context_observed: tuple[object, ...] | None = None
        self._context_loop: asyncio.AbstractEventLoop | None = None

    def _context_registration(self) -> str | None:
        # Read the EFFECTIVE transport, not saved settings or candidate env vars.
        bearer = getattr(self._transport, "_bearer", None)
        if self.backend == "chatgpt" and isinstance(bearer, ChatGPTPlanBearer):
            return bearer.registration_id
        return None

    def _context_auth(self) -> ChatGPTAuthService:
        if self._auth_service is None:
            self._auth_service = chatgpt_auth_service()
        return self._auth_service

    def context_identity(self) -> tuple[object, ...]:
        """Private equality key. Never serialize this into public state/events.

        No network calls. Rereading the private epoch catches external-process
        signout/reauthorization even when there was no process-local callback.
        Ordinary token refresh and reasoning effort do not change this key.
        """
        auth: tuple[object, ...] = ()
        registration = self._context_registration()
        if registration is not None:
            auth = self._context_auth().context_identity(registration)
        return (self.backend, self.model, self.context_window, self.config_error, auth)

    async def context_capacity(self) -> tuple[int | None, str]:
        """Resolve capacity from a BACKGROUND state task, not the chat path.

        ChatGPT is catalog-only (unknown never falls back to configured window).
        API-key/local providers use the runtime's effective configured window.
        """
        if self.backend != "chatgpt":
            value = self.context_window
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value, "configured"
            return None, "unknown"
        registration = self._context_registration()
        if registration is None:
            return None, "unknown"
        identity = self.context_identity()
        try:
            value = await self._context_auth().context_capacity(registration, self.model)
        except Exception:
            return None, "unknown"
        if self.context_identity() != identity:
            return None, "unknown"
        return (value, "catalog") if value is not None else (None, "unknown")

    def subscribe_context(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to no-payload effective changes; return an unsubscribe hook.

        Subscribe on the state manager's running loop: auth saves may run on a
        worker thread and are marshalled back to that loop. This is process-local;
        consumers must also compare context_identity() before accepting results.
        """
        if not self._context_listeners:
            try:
                self._context_loop = asyncio.get_running_loop()
            except RuntimeError:
                self._context_loop = None
            self._context_observed = self.context_identity()
        self._context_listeners.append(callback)
        self._bind_context_auth()

        def unsubscribe() -> None:
            if callback in self._context_listeners:
                self._context_listeners.remove(callback)
            if not self._context_listeners and self._auth_unsubscribe is not None:
                self._auth_unsubscribe()
                self._auth_unsubscribe = None

        return unsubscribe

    def _bind_context_auth(self) -> None:
        if self._auth_unsubscribe is not None:
            self._auth_unsubscribe()
            self._auth_unsubscribe = None
        registration = self._context_registration()
        if self._context_listeners and registration is not None:
            self._auth_unsubscribe = self._context_auth().subscribe(
                registration, self._on_context_auth_change)

    def _on_context_auth_change(self) -> None:
        loop = self._context_loop
        if loop is not None:
            if not loop.is_closed():
                loop.call_soon_threadsafe(self._publish_context_change)
        else:
            self._publish_context_change()

    def _publish_context_change(self) -> None:
        identity = self.context_identity()
        if identity == self._context_observed:
            return
        self._context_observed = identity
        for callback in tuple(self._context_listeners):
            try:
                callback()
            except Exception:
                pass  # state observers must never break auth or provider swaps

    async def effort_support(self) -> EffortSupport:
        """What the current transport can express for the current model.

        Degrade-not-raise: a transport without the optional member, or one whose
        capability lookup fails (e.g. OpenRouter's registry fetch), yields
        all-UNKNOWN rather than blocking the config route.
        """
        resolver = getattr(self._transport, "reasoning_effort_support", None)
        if resolver is None:
            return EffortSupport()
        try:
            return await resolver(self.model)
        except Exception:
            return EffortSupport()

    async def apply_reasoning_effort(self, level: ReasoningEffort | None) -> None:
        """Clamp against the live capability, then push onto the transport."""
        setter = getattr(self._transport, "set_reasoning_effort", None)
        if level is None:
            self.reasoning_effort = None
            self.reasoning_effort_note = None
            if setter is not None:
                setter(None)
            return
        support = await self.effort_support()
        effective, note = support.resolve(level)
        self.reasoning_effort = effective
        self.reasoning_effort_note = note
        if setter is not None:
            setter(effective)

    async def swap(
        self,
        *,
        backend: str,
        model: str | None = None,
        credentials: dict[str, str] | None = None,
        context_window: int | None = None,
        reasoning_effort: ReasoningEffort | None = None,
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
        # The freshly built transport starts with no effort. Re-apply after the
        # ping so an unsupported rung can never block a legitimate model swap —
        # the rung is clamped against the NEW model's capability, which is the
        # whole reason this is re-resolved rather than copied.
        self._transport = transport
        await self.apply_reasoning_effort(
            reasoning_effort if reasoning_effort is not None else self.reasoning_effort
        )
        # Applied only after validation succeeds, for the same reason the engines
        # are: a rejected swap must leave the process exactly as it was. Absent
        # means "unchanged", not "reset" — a model-only hot-swap from the composer
        # must not discard the window the user declared in Settings.
        if context_window is not None:
            self.context_window = context_window
            for sink in self._window_sinks:
                sink.set_window_tokens(context_window)  # type: ignore[attr-defined]
        self._bind_context_auth()
        self._publish_context_change()
        result: dict[str, object] = {"backend": backend, "model": resolved}
        if self.context_window is not None:
            result["context_window"] = self.context_window
        support = await self.effort_support()
        result["reasoning_effort"] = (
            self.reasoning_effort.value if self.reasoning_effort is not None else None
        )
        result["reasoning_effort_note"] = self.reasoning_effort_note
        result["reasoning_effort_support"] = {
            "supported": sorted(level.value for level in support.supported),
            "unsupported": {level.value: why for level, why in support.unsupported.items()},
        }
        return result
