"""Keep the backend alive when the configured provider cannot be constructed.

`build_transport` raises for a provider whose required configuration is absent
(no API key, no base URL). At app import that killed uvicorn outright: the
extension's health poll timed out after 60s and the user got a raw Python
traceback. Worse, it was unrecoverable from inside the product — the Settings
panel is how you correct a wrong provider, and it can only validate against a
LIVE backend. A single mistyped field bricked the install.

Degrading instead of aborting inverts that. The app boots, every route answers,
`GET /v1/config` reports what is wrong, and `PUT /v1/config/provider` (which
builds a fresh transport) becomes the recovery path it was always designed to
be. Only calls that genuinely need the model fail, and they fail with the
provider's own message plus where to fix it.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


class UnconfiguredProviderTransport:
    """Stands in for a transport that could not be built.

    Deliberately not a silent no-op: anything that actually needs the model
    raises, carrying the original construction error. A stub returning empty
    strings would let a broken provider look like a working one that answers
    nothing — the exact confusion this codebase already fights elsewhere.
    """

    # DefaultReasoningEngine reads these at construction, so they must exist or
    # the placeholder would swap one import-time crash for another. False is the
    # honest answer: an unbuilt transport supports no grammar.
    supports_oneof_grammar = False
    supports_anyof_grammar = False
    supports_token_progress = False

    def __init__(self, backend: str, reason: str) -> None:
        self.backend = backend
        self.reason = reason

    def _fail(self) -> RuntimeError:
        return RuntimeError(
            f"The {self.backend} provider is not configured, so this request "
            f"cannot run: {self.reason}. Fix it in Crucible Settings → Provider "
            "(the change applies immediately, no restart needed)."
        )

    async def generate_text(self, **_kwargs: Any) -> str:
        raise self._fail()

    async def generate_json(self, **_kwargs: Any) -> dict[str, object]:
        raise self._fail()


def build_transport_or_placeholder(
    backend: str, build: Callable[[str], Any]
) -> tuple[Any, str | None]:
    """(transport, error) — error is None when the real transport was built.

    `build` is injected rather than imported so this stays unit-testable without
    constructing a real provider.
    """
    try:
        return build(backend), None
    except Exception as exc:  # broad on purpose: every transport raises its own type
        return UnconfiguredProviderTransport(backend, str(exc)), str(exc)
