# Memory redesign phase 3 — shared local-model capability

Date: 2026-10-14. Design: `../specs/2026-10-14-memory-recall-redesign-design.md`.

- [x] Add shared thread-safe disabled/loading/ready/unavailable states and runtime failure
  transitions; opted-out code never loads a model.
- [x] Feed capability states from startup warmup and expose them in `/v1/config`.
- [x] Gate semantic tools, retrieval seed and memory models on current readiness, retaining
  keyword/entity fallbacks. Preserve the process-wide model lock and serial warmup.
- [x] Add the `[local-models]` installer extra and `CRUCIBLE_LOCAL_MODELS` flag, with transitional
  compatibility for existing explicit settings until final removal.
- [x] Add the recommended setup choice, persisted managed-runtime preference, installer
  reconciliation for on/off, and Settings status/control.
- [x] Test capability transitions, failure downgrades, no-local consumers, installer choice,
  setup/settings contracts and regressions; update the design before phase 4.

Phase 4 replaces the legacy judge with the specified LLM fallback. Phase 3 gates the existing
memory path without enabling the new recall pipeline.

## Handoff (2026-10-15)

The shared capability is wired through the production factories and API. Disabled/loading/
unavailable models are excluded from semantic tools, retrieval seeds and memory inference.
Startup warmup and runtime errors update the shared state; failures are terminal until restart.
Legacy injected test indexes remain supported. Explicit CRUCIBLE_LOCAL_MODELS overrides the
legacy per-consumer settings; old environment aliases remain only during the gated overlap.

Managed setup stores the recommended global choice before installing. The unified optional
extra installs code/memory dependencies; opting out uses plain agentd. Changing the choice
rebuilds the managed venv to remove stale optional packages, after stopping all shared backends;
startup/restart reconciles the choice and resumes them. Model caches, credentials and workspace
state are separate and preserved. Settings shows readiness/error state and refreshes on request.

Validation: the backend set passed 261 tests plus the corrected startup smoke check (17 focused
checks passed); one optional semantic-index test was skipped. 125 extension, 13 webview and
57 editor-client tests passed, as did all three TypeScript typechecks. Ruff and strict mypy
passed for the five capability/memory modules; existing large Python modules were checked for
new lint findings against HEAD. No live runtime installation, model download or restart was
performed. Phase 4 now adds the new recall judge and tools behind the v2 flag.

A later continuation fixed wizard re-entry: it loads the persisted local-model choice and
ignores late preference replies once the user makes a new choice. Focused host/setup tests
and TypeScript checks pass; the saved opt-out is no longer silently reset to the default.
