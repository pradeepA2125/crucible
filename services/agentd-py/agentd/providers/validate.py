"""One cheap provider ping. Powers POST /v1/providers/validate and the hot-swap
pre-check. Credentials are request-scoped — used to build the transport, never
persisted, never logged."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agentd.providers.factory import build_transport, resolve_model
from agentd.providers.openai_compatible_transport import proves_json_schema_unsupported


class ProviderValidationError(Exception):
    """Ping failed — message is user-facing and actionable."""


# Backends whose structured-output capability is genuinely unknown until asked.
# Every other entry in the factory points at a vendor we already know, and an
# extra round-trip per validate would buy nothing but latency and quota.
_PROBE_BACKENDS = frozenset({"openai_compatible"})

_PROBE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}

_DEGRADED_WARNING = (
    "No strict JSON schema support; will use json_object + schema-in-prompt. "
    "Expect lower reliability."
)

_INCONCLUSIVE_WARNING = (
    "Could not check strict JSON schema support — the probe failed for an "
    "unrelated reason (rate limit, timeout or network). Crucible detects the "
    "right mode automatically on its first structured call."
)


@dataclass(frozen=True)
class ProviderPingResult:
    """What one validate call learned. A value object: immutable, no behavior —
    the route decides how to render it, and nothing here is ever persisted.

    `json_mode` is None both when the backend was not probed (a known provider)
    and when the probe was inconclusive; `warning` distinguishes them. That is
    deliberate: an "unknown" sentinel would be a third vocabulary word every
    consumer would have to learn in order to render it as "no information".
    """

    model: str
    json_mode: str | None = None
    warning: str | None = None


async def ping_transport(transport: object, model: str, timeout_sec: float = 30.0) -> None:
    try:
        await asyncio.wait_for(
            transport.generate_text(  # type: ignore[attr-defined]
                model=model,
                system_instructions="Reply with the single word OK.",
                user_payload={"ping": True},
            ),
            timeout=timeout_sec,
        )
    except TimeoutError:
        raise ProviderValidationError(
            f"Provider did not respond within {timeout_sec:.0f}s"
        ) from None
    except Exception as exc:  # surface the provider's own message — it names the fix
        raise ProviderValidationError(str(exc)) from exc


async def probe_json_mode(
    transport: object, model: str, timeout_sec: float = 30.0
) -> str | None:
    """One tiny strict json_schema call. Returns "strict", "json_object", or None
    when the attempt proved nothing either way.

    Informational ONLY — it does not configure the transport and nothing here is
    cached or persisted. The transport rediscovers its own mode at runtime via the
    sticky downgrade, at a cost of at most one failed call per process, so there is
    no capability record that can go stale.

    Never raises: validation has already succeeded by the time this runs, and a
    probe failure must not turn a working provider into ok:false.
    """
    raised: Exception | None = None
    try:
        await asyncio.wait_for(
            transport.generate_json(  # type: ignore[attr-defined]
                model=model,
                schema_name="CapabilityProbe",
                schema=_PROBE_SCHEMA,
                system_instructions="Return JSON only.",
                user_payload={"probe": True},
            ),
            timeout=timeout_sec,
        )
    except Exception as exc:
        raised = exc

    # The transport's own sticky verdict is authoritative wherever it has one.
    # "No exception" is NOT evidence of strict support: generate_json catches a
    # rejected strict call, falls back to json_object and returns a perfectly
    # valid dict — so the endpoint this whole feature exists for looks identical
    # to a fully capable one from out here.
    reported = getattr(transport, "json_mode", None)
    if reported == "json_object":
        return "json_object"
    if raised is None:
        return "strict"
    if reported is not None:
        # The transport ran, weighed the failure against the same asymmetry below,
        # and declined to downgrade (or the json_object fallback failed for reasons
        # of its own). Either way we learned nothing about response_format.
        return None
    # A transport that keeps no such state: apply that asymmetry directly. A 429,
    # a timeout or a broken stream is a load/availability signal, never a statement
    # about schema support — and telling a user their good endpoint is degraded
    # pushes them toward a worse configuration on the strength of one blip.
    return "json_object" if proves_json_schema_unsupported(raised) else None


async def ping_provider(
    backend: str, model: str | None = None, credentials: dict[str, str] | None = None
) -> ProviderPingResult:
    try:
        transport = build_transport(backend, credentials=credentials)
        resolved = model or resolve_model(backend)
    except Exception as exc:
        # Broad on purpose: transports raise RuntimeError at construction when a
        # key is missing ("OPENAI_API_KEY is required…") — that IS the actionable
        # message the wizard should show, not a 500.
        raise ProviderValidationError(str(exc)) from exc
    await ping_transport(transport, resolved)

    if backend not in _PROBE_BACKENDS:
        return ProviderPingResult(model=resolved)

    # This transport is a throwaway built for the ping, so the probe's one real
    # side effect — the transport's sticky downgrade — dies with it. It never
    # touches the live engines' transport.
    json_mode = await probe_json_mode(transport, resolved)
    if json_mode == "json_object":
        return ProviderPingResult(
            model=resolved, json_mode=json_mode, warning=_DEGRADED_WARNING
        )
    if json_mode is None:
        return ProviderPingResult(model=resolved, warning=_INCONCLUSIVE_WARNING)
    return ProviderPingResult(model=resolved, json_mode=json_mode)
