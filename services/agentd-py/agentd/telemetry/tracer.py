"""The tracer provider's lifecycle (observability spec §6.1).

OpenTelemetry is imported only inside `init_tracing` and `install_test_exporter`, so with
tracing off no `opentelemetry*` module is ever loaded. The provider is kept here rather than
installed as OpenTelemetry's global: the global can be set once per process, which tests
and a re-init after shutdown both need to get around."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from agentd.telemetry.config import TracingConfig

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)

_INSTALL_HINT = "pip install 'crucible-agentd[telemetry]'"
# Seconds one export may take; keeps a dead endpoint from holding the exporter thread.
_EXPORT_TIMEOUT_SEC = 5

_provider: Any = None
_tracer: Tracer | None = None
_capture_content = False
_warned_missing = False


def init_tracing(config: TracingConfig, *, version: str, backend: str) -> bool:
    """Start exporting spans. True when spans will export; never raises."""
    global _warned_missing
    if not config.enabled:
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        if not _warned_missing:
            _warned_missing = True
            logger.warning(
                "CRUCIBLE_TRACING_ENABLED is set but the tracing packages are not installed; "
                "tracing stays off. Install them with: %s", _INSTALL_HINT)
        return False
    try:
        provider = TracerProvider(resource=Resource.create({
            "service.name": "crucible-agentd",
            "service.version": version,
            # Phoenix routes spans to a project by this resource attribute.
            "openinference.project.name": config.project,
            "crucible.backend": backend,
        }))
        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=config.endpoint, timeout=_EXPORT_TIMEOUT_SEC)))
    except Exception:  # noqa: BLE001 — tracing must never stop the backend from starting
        logger.warning("[telemetry] could not start tracing", exc_info=True)
        return False
    _install(provider, config.capture_content)
    logger.info("[telemetry] exporting traces to %s (content %s)", config.endpoint,
                "on" if config.capture_content else "off")
    return True


def _install(provider: Any, capture_content: bool) -> None:
    global _provider, _tracer, _capture_content
    _provider = provider
    _tracer = provider.get_tracer("agentd")
    _capture_content = capture_content


def is_active() -> bool:
    return _tracer is not None


def capture_content() -> bool:
    return _tracer is not None and _capture_content


def get_tracer() -> Tracer | None:
    return _tracer


def flush(timeout_sec: float = 5.0) -> bool:
    """Export every finished span now (the eval runner needs them before annotating)."""
    if _provider is None:
        return True
    try:
        return bool(_provider.force_flush(int(timeout_sec * 1000)))
    except Exception:  # noqa: BLE001
        logger.debug("[telemetry] flush failed", exc_info=True)
        return False


async def shutdown_tracing(timeout_sec: float = 2.0) -> None:
    """Flush and stop the exporter, waiting at most `timeout_sec`; never raises."""
    global _provider, _tracer
    provider = _provider
    _provider, _tracer = None, None
    if provider is None:
        return
    try:
        await asyncio.wait_for(asyncio.to_thread(provider.shutdown), timeout=timeout_sec)
    except TimeoutError:
        logger.warning("[telemetry] exporter did not finish within %.1fs; spans may be lost",
                       timeout_sec)
    except Exception:  # noqa: BLE001
        logger.debug("[telemetry] shutdown failed", exc_info=True)


def install_test_exporter(*, capture_content: bool = True) -> InMemorySpanExporter:
    """Tests only: export synchronously into memory, replacing any earlier install."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    reset_for_tests()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _install(provider, capture_content)
    return exporter


def reset_for_tests() -> None:
    global _provider, _tracer, _capture_content, _warned_missing
    _provider, _tracer, _capture_content, _warned_missing = None, None, False, False
