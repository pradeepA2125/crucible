# Feature Roadmap — Copilot-Agent Parity & Beyond

**Status:** Draft for phase-by-phase execution · **Date:** 2026-06-29 · **Owner:** pradeep
**Scope:** The post-memory-harness feature arc. Each phase ships independently, exits on a clear criterion, and gets its own `writing-plans` implementation plan when we start it.

---

## Positioning (the wedge this roadmap defends)

We do not chase Cursor/Copilot on **tab-completion, raw speed, or distribution** — we lose there. We compete on **correctness and continuity**: an agent with a real LSP-resolved symbol graph + **durable, inspectable cross-session memory**. Every phase below either (a) closes a *table-stakes* parity gap with Copilot agent mode so we're not dismissed, or (b) deepens the wedge. We **adopt open standards** (agentskills.io, MCP) rather than inventing formats — instant ecosystem compatibility, zero lock-in.

## Baseline — what's already shipped (on `main`, 2026-06-29)

- **Agentic core:** spec-first task lifecycle (shadow→promote), ReAct planning + execution loops, verify-phase state machine, delta replan, cooperative abort, durable telemetry, task narrative.
- **Reactive chat controller** (`AI_EDITOR_CHAT_CONTROLLER`): ModeGate, clarify gate, todo ledger, inline ACID edits with instant-promote.
- **Retrieval:** Rust incremental indexer (tree-sitter + LSP-resolved Calls/Inherits/Implements), `query_graph` tool.
- **Memory harness P1–P3:** compaction, cross-session recall + write path, cross-encoder reranker, and the **read-only inspector panel** (recall-trace + browser tabs) — merged + CDP-verified.
- **Multi-provider:** anthropic/openai/gemini/groq/ollama/watsonx/openrouter.
- **Task subsystem** exists but is **flag-gated OFF** (`AI_EDITOR_TASK_SUBSYSTEM`) — the dormant substrate Phase 5 revives.

## Parity scorecard vs Copilot agent mode (VS Code)

| Capability | State | Phase |
|---|---|---|
| Autonomous multi-step agent + terminal/tests + self-heal | ✅ on par | — |
| Multi-model | ✅ ahead | — |
| Cross-session memory + inspectable recall | ✅ **ahead** (wedge) | done |
| Symbol-graph retrieval | ✅ ahead | done |
| Diff review + revert | ✅ on par (granular per-hunk: gap) | P6 |
| Project instructions / prompt files | ✅ on par (done, on branch) | P1 |
| Agent Skills (agentskills.io) | ✅ done (on branch) | **P2** |
| MCP client (+ GitHub integration) | ✅ done (on branch) | **P3** |
| Polished UI / settings / one-command install | ❌ | **P4** |
| Subagents / custom agents / code-review agent | ❌ (dormant task path) | **P5** |
| Per-hunk accept, vision, tab-completion, cloud agent | ❌ | **P6 / non-goal** |

## Guiding principles

1. **Adopt standards, don't invent** — agentskills.io for skills, MCP for tools.
2. **Capability first, polish at a milestone** — P1–P3 land with minimal/headless config; P4 is the dedicated UI/settings/install pass that *surfaces and packages* them before the heavier P5 work.
3. **Lean on existing seams** — `ToolSource`/`ToolRegistry`, controller system-prompt assembly (the gated teaching-block pattern), the memory `Embedder`/recall scorer, the dormant task path.
4. **Flag-gate every phase** (`AI_EDITOR_*`) — ship dark, enable when proven, identical to how memory/task/controller already gate.
5. **Each phase exits green** — TS suites + py suites + typecheck + a live smoke before it's "done."

---

## Phase overview

| Phase | Theme | Effort | Leverage | Depends on |
|---|---|---|---|---|
| **P1** | Project instructions & prompt files | Low | High (improves every run) | — |
| **P2** | Agent Skills (open standard) | Low-Med | **High** | P1 (prompt-assembly seam) |
| **P3** | MCP client + GitHub integration | Med | High | — (parallel to P2) |
| **P4** | Polished UI, settings & installation | Med-High | High (productization) | surfaces P2/P3 config |
| **P5** | Subagents & custom agents | Med-High | Med | P2 (skills), dormant task path |
| **P6** | Deferred polish (per-hunk, vision, …) | varies | Low-Med | — |

---

## Phase 1 — Project Instructions & Prompt Files

**Goal:** Honor a project-level instructions file (auto-injected into every agent run) and reusable prompt files — the cheapest Copilot parity win, and the foundation the later prompt-assembly work builds on.

**Why here:** One of the lowest-effort, highest-frequency wins; every subsequent phase's agents benefit. Establishes the convention + injection seam Phase 2 (skills) reuses.

**Scope:**
- Discover a project instructions file — **`AGENTS.md` is the primary convention** (broad cross-tool compatibility, low effort); also read `.github/copilot-instructions.md` as a fallback. Inject into the planning + controller system prompts.
- Prompt files: a `.ai-editor/prompts/` folder; `/name` in the composer expands the saved prompt with arg substitution.
- Precedence + size budgeting (instructions are always-on; keep them lean — reuse the compaction budget discipline).

**Architecture seams:** `planning/prompts.py` + `chat/controller_prompts.py` system-prompt builders (same place `task_subsystem`/`memory` teaching blocks are gated); chat composer command parsing.

**Exit criteria:** an `AGENTS.md` measurably steers a live run; `/prompt-name` expands in chat; flag `AI_EDITOR_PROJECT_INSTRUCTIONS`; tests + live smoke green.

**Decided:** `AGENTS.md` primary (+ `.github/copilot-instructions.md` fallback). Per-directory nested instructions deferred.

### ✅ Status: DONE (2026-06-30, branch `docs/copilot-parity-roadmap`, not yet merged to `main`)

Spec: `docs/superpowers/specs/2026-06-29-project-instructions-prompt-files-design.md` · Plan: `docs/superpowers/plans/2026-06-29-project-instructions-prompt-files.md`. Implemented TDD task-by-task; documented in `CLAUDE.md` ("Project instructions (AGENTS.md) + prompt files (P1)").

**What shipped:**
- **Project instructions (backend, controller-only):** `agentd/instructions/loader.py::ProjectInstructionsLoader` — mtime-cached `<workspace>/AGENTS.md` reader (self-updates on edit with no restart; best-effort; size-capped). Injected into the **controller** system prompt via `format_controller_system_prompt(project_instructions=…)` (a new labeled block, `.replace`-safe for literal `{}`), wired through `DefaultReasoningEngine.create_controller_step` + `controller_factory`. Flags: `AI_EDITOR_PROJECT_INSTRUCTIONS` (default **ON**, kill-switch) + `AI_EDITOR_INSTRUCTIONS_MAX_CHARS` (default 16000).
- **Prompt files (frontend-only, expand-before-send):** `.ai-editor/prompts/<name>.md` expanded in the composer via `/name [args]` (`$ARGUMENTS` + `$1..$N`). `src/prompt-files.ts` helpers → `controller.ts listPrompts/expandPrompt` → `chat-panel.ts`/`extension.ts` plumbing → `InputArea.tsx` expand-before-send (webview mirrors `parseSlashCommand` in `webview-ui/src/slash.ts`). **No backend route / no editor-client contract change.**

**Scope deviations from the original plan (intentional):**
- **AGENTS.md only** — dropped the `.github/copilot-instructions.md` fallback (single source, simpler; revisit if requested).
- **Controller-only injection** — did NOT touch `planning/prompts.py`/the task path (the task subsystem is flag-gated OFF; the controller is the live path).

**Verification:** full suites green — Python 1061 pass/1 skip; TS 308 (editor-client 45 + vscode-extension 59 + webview-ui 205); typecheck + builds clean. **Live-smoke verified**: AGENTS.md text reached the real LLM system prompt + self-updated FOX→OWL with no restart (artifact-confirmed on a running backend); `/summarize <file>` expansion driven end-to-end in the VS Code dev host via CDP.

**Post-merge bug found + fixed (`fd6fef3`):** an unmatched `/name` (no such prompt file) made the composer a silent dead-end (Enter/Send did nothing). Now `found=false` sends the typed text as a normal message. Unit-tested + live-verified.

**Incidental infra fixes (committed):** `agentd-py` editable install was pointed at a stale worktree; `pip install -e .` broke on flat-layout discovery (pinned `packages.find = ["agentd*"]`); full pytest couldn't collect (`pythonpath = ["."]` for `from tests.` helpers).

**Deferred (optional follow-ups, not blockers):** an inline "no prompt named X" hint (vs. send-as-text on miss); `/`-autocomplete suggestion list; per-directory nested instructions; planning/task-path injection if the task subsystem is ever enabled.

---

## Phase 2 — Agent Skills (agentskills.io open standard)

**Goal:** Discover and progressively-disclose `SKILL.md` skills, so specialized playbooks (+ bundled scripts) load on relevance — matching Copilot's Skills, and **compatible with the existing community ecosystem** because we implement the open standard, not a bespoke format.

**Why here:** Highest-leverage extensibility for us specifically — the relevance-matching infra (the memory `Embedder` + recall scorer) **already exists**, and adopting agentskills.io means users' existing `.claude/skills` / `.agents/skills` work in our editor on day one. Pure prompt-assembly + file discovery; no transport/protocol work (unlike P3).

**Scope:**
- Discover `SKILL.md` (YAML frontmatter `name`+`description`+optional `license`, markdown body) from project (`.github/skills`, `.claude/skills`, `.agents/skills`) and personal (`~/.agents/skills`) dirs.
- **Progressive disclosure:** catalog name/description always; inject a skill's body only when its description is relevant to the turn (rank via the existing `Embedder`/recall scorer; cap N + token budget via the compaction discipline).
- **Skill scripts as tools:** a `SkillToolSource` exposing a skill's bundled scripts via `run_command` (existing shell + policy gate).
- Headless config in this phase (enable/list via env/flag); the management UI lands in P4.

**Architecture seams:** new `agentd/skills/` module (`SkillSource`); inject through the same system-prompt assembly as P1; relevance via `agentd/memory/embedder.py` + recall scoring; scripts via `tools/shell.py` + `ToolRegistry`.

**Effort:** Low-Med (discovery + injection is small; relevance infra is reused).

**Exit criteria:** a project `SKILL.md` is discovered, relevance-gated, and demonstrably changes agent behavior on a matching task; a skill-bundled script runs through the shell gate; flag `AI_EDITOR_SKILLS_ENABLED`; tests + live smoke green.

**Risks / Open Qs:** context budgeting (when to load a body, eviction) — mitigated by reusing compaction/recall budgeting; security of running skill-bundled scripts (route through the existing shell-policy gate; never auto-run без approval); precedence vs project instructions (P1).

### ✅ Status: DONE (2026-06-30, branch `docs/copilot-parity-roadmap`, not yet merged to `main`)

Spec: `docs/superpowers/specs/2026-06-30-agent-skills-design.md` · Plan: `docs/superpowers/plans/2026-06-30-agent-skills.md`. Documented in `CLAUDE.md` ("Agent Skills (P2, copilot-parity roadmap)").

**What shipped:** `.ai-editor/skills/<name>/SKILL.md` discovery (`agentd/skills/`: loader + models + catalog + config + tool_source), a budget-guarded catalog injected into the controller system prompt, model-driven `read_skill(name)` loading a skill's body into the dynamic payload tail (`active_skills`, re-injected every iteration, compaction-resilient), and `/skill` deterministic forced-load (`forced_skills` message field seeds `active_skills` before iteration 1). Flag `AI_EDITOR_SKILLS_ENABLED`, default OFF, controller-only. `rank_skills_by_relevance` (the scale path, reuses the memory `Embedder`) is built + tested but not wired live — v1 uses order-truncation, sufficient until catalog size demands query-ranking.

**2026-07-02 follow-up — closed the model-driven activation gap:** the initial live-smoke (2026-06-30) concluded model-driven `read_skill` was a "judgment gap" on local/weak models and that `/skill` forced-load was the only reliable activation path. Found + fixed the actual wiring bug behind that: the skill-check hint only ever fired on a thread's literal first-ever message (`if not history:`, but `history` seeds from the whole thread's replayed conversation) — every later message in an ongoing thread silently never got the check. Fixed via a run-scoped `decide_entry` flag. Also removed two self-inflicted prompt anti-patterns (a hardcoded example-category list mirroring the installed catalog; a competing "FIRST action MUST" claim stacked right after the skill-check's own) and added a few-shot worked example + explicit "unconditional" framing (technique borrowed from comparing against Anthropic's own Claude Skills system-prompt approach). **Model-driven `read_skill` now fires 2-for-2 live on TQP/qwen3.6:35b** (a creative-work request → `brainstorming`; a real bug report → `systematic-debugging`), no forced-load needed, each on turn 2+ of an ongoing thread — the realistic case the original test missed.

**Verification:** full py suite green (1091 tests collected, 1 skip, no failures) after the 2026-07-02 fix; original 2026-06-30 landing verified at py 1062+/1 skip + TS 316. Live-smoke verified both dates (backend :8000 then :8002, TQP, `shadow-forge-stress`).

**Deferred (unchanged from original plan):** the unified `/`-autocomplete dropdown (prompts+skills badged) → **P4**; wiring `rank_skills_by_relevance` to the tail until catalog size demands it; broader cross-provider validation (only TQP/qwen tested so far — gemini has a pre-existing empty-tool-args issue unrelated to skills, per `CLAUDE.md`).

---

## Phase 3 — MCP Client + GitHub Integration

**Goal:** Connect external MCP tool servers (databases, web, **GitHub**), matching Copilot's core extensibility story on the open MCP standard.

**Why here:** Keystone *tool* extensibility; closes GitHub issue/PR/branch ops **for free** by pointing at the GitHub MCP server. Parallelizable with P2 (different seam: tools vs instructions).

**Scope:**
- MCP client (stdio + HTTP transports): connect configured servers, list tools/resources, surface them as an `McpToolSource` in `ToolRegistry`.
- Server config (headless in this phase: a config file / env; the management pane lands in P4).
- Tool-call approval reuses the existing command/scope decision gate model (don't invent a new approval path).
- GitHub MCP server wired as the reference integration (issues, PRs, branches).

**Architecture seams:** `ToolSource`/`ToolRegistry` (an MCP client is just another `ToolSource`); approval via the existing gate infra; both ReAct loops pick the tools up via `registry.definitions()`.

**Effort:** Med (transport + lifecycle + schema mapping is the real work).

**Exit criteria:** a configured MCP server's tools are callable by the agent end-to-end (live), gated by approval; GitHub MCP demonstrably opens a PR / reads an issue; flag `AI_EDITOR_MCP_ENABLED`; tests + live smoke green.

**Risks / Open Qs:** server lifecycle/health + timeouts (reuse provider-retry discipline); governance (which servers allowed) — minimal allowlist now, richer policy later.

---

## Phase 4 — Polished UI, Settings & Installation  ⟵ *productization milestone (before subagents)*

**Goal:** Turn the capability stack (P1–P3) into a **shippable, installable, configurable product** — the moment a new user can install and drive it without the current manual venv/start-backend/dev-host dance.

**Why here (explicit):** P1–P3 deliberately ship headless/minimal-config. Before investing in the heavier subagent work (P5), we make the product *usable by someone other than us*: one-command install, a real settings surface (which now has things worth configuring — providers, memory, skills, MCP), and UI polish across the panels.

**Scope:**
- **Installation:** one-command setup (backend venv + deps + extension build + launch), packaged so a user runs ~one script (or a VSIX install + auto-backend-spawn). Collapse the dual-instance dev complexity. Decide distribution target (see Open Qs).
- **Settings pane (`.env` → configurable UI):** surface today's env-only knobs as real settings — provider + **dynamic model selection/switching** (change provider/model at runtime, not via restart), API keys (secure storage via VS Code SecretStorage, never in logs), memory flags (enabled/reranker/budgets), scope/shell policy, **Skills management** (list/enable/disable — surfaces P2), **MCP server management** (add/remove/health — surfaces P3).
- **`@`-file mentions:** typing `@path` in the composer references a file; the rendered mention is **clickable to open that path** in the editor (and feeds the path into the turn's context).
- **UI polish pass:** consistent design system across chat + review + memory inspector (resolve the inspector's hardcoded slate palette → theme-adaptive or a deliberate locked theme), onboarding/walkthrough, empty states, error surfaces — polishing the current `webview-ui` in place (no net-new design system).
- **Packaging:** extension manifest, icon/branding, README + Marketplace listing copy; publish to the **VS Code Marketplace** (the decided distribution target).

**Architecture seams:** VS Code `contributes.configuration` + a settings webview; the extension activation/install flow; `start-backend.sh` → a managed backend spawn; the React `webview-ui` design system (the inspector + chat share it).

**Effort:** Med-High (breadth, not depth).

**Exit criteria:** a clean machine goes from zero → working editor via the documented install path; settings pane round-trips all config (incl. skills + MCP) and a key change takes effect; design pass reviewed against wireframes; smoke on a fresh profile.

**Decided:** distribution target = **VS Code Marketplace** (standalone app later); P4 polishes `webview-ui` in place (no rebrand). **Still open:** secure secret storage (use VS Code SecretStorage); whether the Python backend ships **bundled with the extension** or as a **managed local process** the extension spawns — this is the main remaining packaging question for a Marketplace install.

---

## Phase 5 — Subagents & Custom Agents

**Goal:** Forked-context subagents (a sub-loop whose final result returns to the parent) and custom agent personas — matching Copilot's subagents/custom agents/Agent Skills-in-forked-context, and enabling a **code-review agent**.

**Why here (after polish):** It's the heaviest remaining parity item and benefits from P2 (skills run *in* subagents) and a stable, configurable product to host agent management. CLAUDE.md already flags "turning the task path into a sub-agent execution path" as the deferred design — this phase executes it.

**Scope:**
- Revive the **dormant task path** as a forked-context execution unit (its own ToolLoop, isolated history, returns a result to the parent controller).
- Custom agent personas (`.agent.md`-style: tools + instructions + frontmatter) — discover, select, run.
- **Code-review agent** as the first built-in persona (reviews a diff; rides P5 + existing diff infra).
- Skills-in-subagent: large/irrelevant-intermediate skills (P2) execute forked, return only the result.
- Management UI in the P4 settings surface.

**Architecture seams:** the flag-gated task subsystem (`AI_EDITOR_TASK_SUBSYSTEM`) becomes the subagent executor; controller spawns/awaits subagents; persona files via the P1/P2 discovery patterns.

**Effort:** Med-High.

**Exit criteria:** a parent turn dispatches a subagent that completes a scoped task and returns a result without leaking intermediate context; a custom persona runs; the code-review agent reviews a real diff; flag-gated; tests + live smoke green.

**Risks / Open Qs:** context isolation + budgeting across parent/child; concurrency (single-process asyncio race discipline already established); cost/loop bounds.

---

## Phase 6 — Deferred Polish & Non-Goals

Committed only if data/users demand it:
- **Granular per-hunk accept/undo** in the review surface (extend ReviewCard/DiffPanes).
- **Vision / multimodal input** (provider-dependent; add image parts to payloads).
- **Next-edit suggestions / tab completion** — *explicit non-goal for now* (not our wedge; highest effort; competes on Copilot's turf).
- **Cloud / background agent** (assign issue → remote run → PR) — *explicit non-goal for now* (heavy infra; local-first is fine until there's pull).

---

## Cross-cutting (every phase)

- **Flag-gated, default-off** until proven; coherence warnings for incompatible flag combos (pattern exists).
- **Testing:** unit (TS vitest + py pytest) + typecheck + a **live smoke** (CDP for webview, curl/scripts for backend) before "done" — the discipline that caught the inspect-route bug and the wireframe mismatch this cycle.
- **Docs:** update `CLAUDE.md` architecture section per phase; spec+plan under `docs/superpowers/` per phase.
- **Design:** check `.superpowers/brainstorm/*/content/*.html` for approved wireframes **before** building any UI (the lesson from P3-B).

## How we execute this

Per phase, when we start it: run `superpowers:brainstorming` if the design isn't settled, then `superpowers:writing-plans` to produce the bite-sized implementation plan, then execute. This roadmap is the index; each phase spawns its own plan doc.

## Decisions resolved before P1 kickoff

1. **Distribution target:** **VS Code Marketplace extension first**; a standalone application comes later (future, post-P4). This anchors P4 packaging on the Marketplace path and stores P1–P3 config in workspace/user files the extension reads, not a bespoke installer.
2. **Filename conventions:** **`AGENTS.md` is the primary instructions convention** (broad cross-tool compatibility, low effort), with `.github/copilot-instructions.md` as a fallback; skills read the community dirs (`.github/skills`, `.claude/skills`, `.agents/skills`). We favor *honoring existing ecosystem files* over a single bespoke `.ai-editor/` namespace.
3. **P4 scope is polish-to-standard, not a rebrand:** polish the current React `webview-ui` in place to meet general release-quality bars — concretely: dynamic model selection/switching, `.env`-style config surfaced through the settings pane, clickable `@`-file mentions that open the referenced path, plus the usual marketplace packaging. No net-new design system.
