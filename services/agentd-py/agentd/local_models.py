"""Runtime readiness shared by code retrieval and memory; no optional ML imports."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal

ModelKind = Literal["embedder", "reranker"]
ModelState = Literal["disabled", "loading", "ready", "unavailable"]


@dataclass(frozen=True)
class ModelCapability:
    state: ModelState
    error: str | None = None


class LocalModels:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        initial = ModelCapability("loading" if enabled else "disabled")
        self._states: dict[ModelKind, ModelCapability] = {
            "embedder": initial, "reranker": initial,
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> LocalModels:
        # Managed installs send an explicit choice; manual installs retain the old default.
        return cls(env.get("CRUCIBLE_LOCAL_MODELS", "1").strip().lower() in {
            "1", "true", "yes", "on",
        })

    def state(self, kind: ModelKind) -> ModelCapability:
        with self._lock:
            return self._states[kind]

    def ready(self, kind: ModelKind) -> bool:
        return self.state(kind).state == "ready"

    def mark_ready(self, kind: ModelKind) -> None:
        with self._lock:
            # Failure is terminal until restart, even if a racing warmup succeeds.
            if self._states[kind].state == "loading":
                self._states[kind] = ModelCapability("ready")

    def fail(self, kind: ModelKind, error: object) -> None:
        with self._lock:
            if self._states[kind].state not in {"disabled", "unavailable"}:
                self._states[kind] = ModelCapability(
                    "unavailable", (str(error) or type(error).__name__)[:500],
                )

    def disable(self, kind: ModelKind) -> None:
        with self._lock:
            self._states[kind] = ModelCapability("disabled")

    def payload(self) -> dict[str, object]:
        with self._lock:
            return {kind: asdict(state) for kind, state in self._states.items()}


def semantic_ready(index: object | None) -> bool:
    """Legacy injected indexes keep working; production indexes carry the capability."""
    if index is None:
        return False
    capability = getattr(index, "local_models", None)
    return not isinstance(capability, LocalModels) or capability.ready("embedder")
