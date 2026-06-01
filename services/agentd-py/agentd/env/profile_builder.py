"""Composes EcosystemProbe + draft_conventions LLM call → EnvProfile."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agentd.domain.models import EnvEcosystemEntry, EnvProfile
from agentd.env.probe import EcosystemProbe, ProbeResult


class _Reasoner(Protocol):
    async def draft_conventions(self, *, probe: ProbeResult) -> dict: ...


class EnvProfileBuilder:
    """Build an EnvProfile via deterministic probe + one LLM call.

    Failure mode: any unrecoverable error in the LLM call yields a
    `bootstrap_needed=True` profile with a diagnostic; the caller (orchestrator)
    still persists it so the agent uses find_binary/init_workspace going forward.
    """

    def __init__(self, *, reasoner: _Reasoner) -> None:
        self._reasoner = reasoner

    async def build(self, workspace_root: Path) -> EnvProfile:
        probe = await EcosystemProbe.scan(workspace_root)
        now = datetime.now(timezone.utc)

        # No manifests → no LLM call.
        if not probe.ecosystems:
            return EnvProfile(
                workspace_root=probe.workspace_root,
                built_at=now,
                bootstrap_needed=True,
                ecosystems=[],
                conventions_notes=None,
                diagnostics=[*probe.diagnostics, "no manifests found in workspace"],
            )

        # LLM call: try once + one retry on any exception.
        last_err: Exception | None = None
        decision: dict | None = None
        for _ in range(2):
            try:
                decision = await self._reasoner.draft_conventions(probe=probe)
                break
            except Exception as exc:  # noqa: BLE001 — message surfaced in diagnostic
                last_err = exc

        if decision is None:
            return EnvProfile(
                workspace_root=probe.workspace_root,
                built_at=now,
                bootstrap_needed=True,
                ecosystems=[],
                conventions_notes=None,
                diagnostics=[
                    *probe.diagnostics,
                    f"convention drafting failed: {last_err}",
                ],
            )

        entries = [EnvEcosystemEntry(**e) for e in decision.get("ecosystems", [])]
        return EnvProfile(
            workspace_root=probe.workspace_root,
            built_at=now,
            bootstrap_needed=False,
            ecosystems=entries,
            conventions_notes=decision.get("conventions_notes"),
            diagnostics=list(probe.diagnostics),
        )
