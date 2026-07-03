"""One cheap provider ping. Powers POST /v1/providers/validate and the hot-swap
pre-check. Credentials are request-scoped — used to build the transport, never
persisted, never logged."""
from __future__ import annotations

import asyncio

from agentd.providers.factory import build_transport, resolve_model


class ProviderValidationError(Exception):
    """Ping failed — message is user-facing and actionable."""


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


async def ping_provider(
    backend: str, model: str | None = None, credentials: dict[str, str] | None = None
) -> str:
    try:
        transport = build_transport(backend, credentials=credentials)
        resolved = model or resolve_model(backend)
    except Exception as exc:
        # Broad on purpose: transports raise RuntimeError at construction when a
        # key is missing ("OPENAI_API_KEY is required…") — that IS the actionable
        # message the wizard should show, not a 500.
        raise ProviderValidationError(str(exc)) from exc
    await ping_transport(transport, resolved)
    return resolved
