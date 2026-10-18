"""Keep query-independent discovery caches current, without running searches or models."""
from __future__ import annotations

import logging
import threading
from typing import Protocol

log = logging.getLogger(__name__)


class WarmClient(Protocol):
    def snapshot_version(self, workspace_path: str) -> object: ...
    def warm_discovery(self, workspace_path: str) -> dict[str, object]: ...


class DiscoveryWarmer:
    """One coalescing worker per workspace; polling covers missed index notifications."""

    def __init__(self, client: WarmClient, workspace: str, *, poll_seconds: float = 1,
                 debounce_seconds: float = .15) -> None:
        self.client = client
        self.workspace = workspace
        self.poll_seconds = poll_seconds
        self.debounce_seconds = debounce_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run,
                                        name="discovery-cache-warmup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def notify(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        previous: object = object()
        first = True
        while not self._stop.is_set():
            try:
                version = self.client.snapshot_version(self.workspace)
                if version != previous or self._wake.is_set():
                    self._wake.clear()
                    if not first and self._stop.wait(self.debounce_seconds):
                        return
                    version = self.client.snapshot_version(self.workspace)
                    self.client.warm_discovery(self.workspace)
                    previous = version
                    first = False
            except Exception:
                log.debug("Discovery cache background warmup failed", exc_info=True)
            self._wake.wait(self.poll_seconds)
