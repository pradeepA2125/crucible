# P2 — Agent Skills (agentskills.io) — Design

**Status:** Approved design, pre-implementation · **Date:** 2026-06-30 · **Owner:** pradeep
**Roadmap:** Phase 2 of `docs/superpowers/2026-06-29-feature-roadmap-copilot-parity.md`
**Next:** `writing-plans` → implementation plan.

---

## 1. Goal

Discover and progressively-disclose **agentskills.io `SKILL.md` skills** so specialized playbooks
(+ bundled scripts) load on relevance — matching Copilot/Claude/Codex Skills, and compatible with
the community ecosystem because we implement the **open standard**, not a bespoke format.

A skill is a directory `<name>/SKILL.md` (YAML frontmatter `name`+`description`, markdown body)
with optional `scripts/`, `references/`, `assets/`. The agent sees a lightweight catalog of all
skills always; it pulls a full body into context **only when relevant** (model-driven), and may run
a skill's bundled scripts through the existing shell gate.

This plugs into the same seams P1 used: the **controller system-prompt assembly** (gated teaching
blocks), the **`ToolSource`/`AggregatingToolRegistry`** composite, the **dynamic payload tail** (where
`recalled_memories` already ride), and the **VS Code composer** `/`-command path.

## 2. Decisions (resolved during brainstorming)

Grounded in a survey of the canonical spec + four real implementations (Claude Code, Codex,
opencode native, opencode-agent-skills plugin) — see §10 for the evidence that drove each.

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Single `.ai-editor/skills/<name>/SKILL.md` dir** for v1 | Simplest discovery, consistent with P1's `.ai-editor/prompts/`. Files keep the **standard SKILL.md format** so they stay portable; ecosystem dirs (`.claude/skills`, `~/.agents/skills`) are a trivial later add (append to a dir list). |
| 2 | **Model-driven progressive disclosure** | The standard (and every real impl) always loads the name+description **catalog**, then lets the model decide to load a body. No embedding needed for the common case; a cosine threshold is a worse relevance judge than the model. |
| 3 | **Budget-gated catalog ranking is the dormant scale path** | Real impls cap the catalog (Codex: 8000 chars). Under budget → show all (v1). Over budget → rank descriptions by `Embedder` cosine vs the turn query, show top entries. Wired-but-dormant; the seam P3's MCP Tool-RAG reuses. |
| 4 | **`read_skill(name)` tool loads the body into the dynamic context tail** | Universal real-impl pattern (`use_skill`/`skill`). Tail placement (not a system-prompt rebuild) is KV-safe (finding #13) and lets the body survive compaction via per-iteration re-injection. |
| 5 | **Scripts run via the existing `run_command`** (with a worked example) | We already have `run_command` behind the shell-policy gate, and `.ai-editor/skills/` is inside the workspace — so the body cites `scripts/foo.py` and the model shells it through the existing gate. (The plugin's dedicated `run_skill_script` exists only because an opencode plugin has no shell tool — not our situation.) |
| 6 | **`/skill-name` = backend forced-load** | Deterministic explicit invocation (matters for weak local models; maps to the standard's `disable-model-invocation`). Seeds the turn's `active_skills` so the body is pre-loaded into the tail without relying on the model choosing to activate it. |
| 7 | **Default OFF** (`AI_EDITOR_SKILLS_ENABLED`) | New capability — ship dark, enable when proven (cross-cutting principle 4). Unlike P1's always-on instructions (which were table-stakes). |
| 8 | **Controller-only injection** | The planning/task path is dormant (task subsystem off by default); the controller is the live path. Same scope call as P1. |

## 3. Architecture

### 3.1 Discovery + parse (Python backend)

**New module `agentd/skills/loader.py` — `SkillCatalogLoader`** (modeled on
`instructions/loader.py` + `retrieval/graph_walker.py`):

- Constructed with the controller's **frozen `workspace_path`**. Scans `<workspace>/.ai-editor/skills/*/SKILL.md`.
- **mtime-cached, thread-safe.** Re-scans only when the skills dir's mtime moves (a skill added/edited
  self-updates on the next turn — no restart), else returns the cached catalog. Same discipline as
  `ProjectInstructionsLoader`.
- **Frontmatter parse:** YAML between leading `---` markers. Required `name` (≤64 chars, `[a-z0-9-]`)
  + `description` (≤1024 chars). **Warn (don't reject) if `name` ≠ parent folder name.** Optional
  `license`/`compatibility`/`metadata`/`allowed-tools` are parsed-and-ignored (forward-compat).
  A skill missing `name` or `description`, or with unreadable/invalid frontmatter, is **skipped with a
  `logger.warning`** (never crashes the scan — best-effort, same contract as the instructions loader).
- **Output:** `list[SkillManifest]` = `{name, description, body_path, dir}`. The body is **not** read at
  scan time (progressive disclosure — only `read_skill` reads it).

`SkillManifest` is a small dataclass/Pydantic model in `agentd/skills/models.py`.

### 3.2 Catalog injection (controller system prompt)

**`agentd/chat/controller_prompts.py`** — mirrors `_MEMORY_BLOCK` / `_INSTRUCTIONS_BLOCK_TEMPLATE`:

- New `_SKILLS_BLOCK_TEMPLATE` rendered as an `<available-skills>` section (one line per skill:
  `- <name>: <description>`), plus a short teaching paragraph: when to call `read_skill(name)`, and a
  **worked `run_command` example** for bundled scripts
  (`python .ai-editor/skills/<name>/scripts/<file>.py`).
- `format_controller_system_prompt(..., skills_catalog: list[SkillManifest] | None = None)` — appends
  the block when the catalog is non-empty. **Appended, not a placeholder** (cache-stable prefix), after
  the memory + instructions blocks. Uses `.replace` (skill descriptions may contain literal `{}`).
- **Cache stability:** the catalog is mtime-stable across turns (changes only when the skills dir
  changes), exactly like the AGENTS.md block — so the system-prompt prefix stays KV-cache-stable.
- **Budget gate (dormant scale path):** if the rendered catalog exceeds
  `AI_EDITOR_SKILLS_CATALOG_MAX_CHARS` (default high enough that v1 ships full), rank manifests by
  `Embedder` cosine(turn-query, description) and **relocate the ranked subset to the dynamic payload
  tail** (query-dependent ⇒ must not sit in the cached system prompt — finding #13). v1 paths never
  trip this; the ranking code is wired + unit-tested but off by default.

### 3.3 Activation — `read_skill` tool + active-skills tail

**New `agentd/skills/tool_source.py` — `SkillToolSource`** (a `ToolSource`, modeled on
`memory/tool_source.py`):

- Exposes one read-only tool **`read_skill(name)`**. `execute`:
  - Resolves `name` against the catalog. Unknown → `ToolOutput(is_error=True, "no skill named ...")`.
  - Reads the resolved `SKILL.md` body (size-capped at `AI_EDITOR_SKILLS_BODY_MAX_CHARS`, default
    `20000`; over-cap truncates with a marker).
  - **Adds `name` to the turn's `active_skills` set** and returns the body as the tool result.
- Registered in `ChatController._build_registry` (one `sources.append(SkillToolSource(...))` when the
  flag is on), beside `TodoToolSource`/`MemoryToolSource`. `AggregatingToolRegistry` already enforces
  unique tool names.

**Active-skills tail injection** (the "load into the context tail" requirement):

- The controller loop keeps an `active_skills: set[str]` for the turn. Each iteration, the bodies of
  active skills are injected into the **dynamic payload tail** (a new `active_skills` slot in the
  controller payload builder, alongside `recalled_memories`) — KV-safe position, and **re-injected
  every iteration so compaction can't strand an activated body** (our analog of opencode's
  `synthetic:true`/`noReply:true`).
- Both invocation paths converge on this one set: model-driven `read_skill` adds to it mid-turn; the
  `/skill-name` forced-load (§3.4) seeds it before iteration 1.
- v1 scope: `active_skills` is **turn-scoped**. Cross-turn persistence (a thread column) is deferred.

### 3.4 `/skill-name` explicit invocation (backend forced-load)

- **Skills-list endpoint** for composer `/` autocomplete: `GET /v1/skills?workspace=<path>` →
  `[{name, description}]` from the catalog loader. (Read-only; gated by `is_skills_enabled()`.)
- **Forced-load field:** the chat message body
  (`POST /v1/chat/threads/{id}/message`) gains an optional `forced_skills: list[str]`. When present,
  the controller seeds the turn's `active_skills` with those names before the loop starts (each
  validated against the catalog; unknown names dropped with a warning), so the body is in the tail from
  iteration 1 — deterministic, no reliance on the model calling `read_skill`.
- **Frontend (composer):** `/` autocomplete lists **both** prompt files (P1) and skills, visually
  distinguished (a "skill" affordance vs prompt-file). Selecting a skill sets the turn's `forced_skills`
  (it does **not** expand inline like a prompt file — the skill body is large and lives backend-side).
  **Collision rule:** if a name matches both a prompt file and a skill, the **prompt file wins** (P1
  already owns `/name` inline expansion); the autocomplete still lists the skill under its name so the
  user can see it, but the inline-expansion affordance resolves to the prompt file.

### 3.5 Flags (`agentd/chat/controller_factory.py`)

- `is_skills_enabled()` next to `is_memory_enabled` — `AI_EDITOR_SKILLS_ENABLED`, **default OFF**
  (truthy = `1/true/yes/on`). When off: the loader is never built, the catalog block never appends,
  `SkillToolSource` is not registered, `/v1/skills` returns gated-empty, and the composer hides the
  skills affordance (via a `aiEditor.skillsEnabled` `when`-context fed from `/v1/config`, mirroring
  `memoryEnabled`/`taskSubsystemEnabled`).
- `select_chat_handler` builds the `SkillCatalogLoader` from the frozen `workspace_path` when enabled
  (same place the memory harness + instructions loader are wired).

## 4. Components & boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `skills/loader.py::SkillCatalogLoader` | mtime-cached scan + frontmatter parse → `list[SkillManifest]`; degrade-to-skip | filesystem only |
| `skills/models.py::SkillManifest` | `{name, description, body_path, dir}` | — |
| `skills/tool_source.py::SkillToolSource` | `read_skill(name)` → read body, mark active, return | loader, filesystem |
| `controller_prompts.py::_SKILLS_BLOCK` + `format_controller_system_prompt` param | render `<available-skills>` + teaching/example; append when non-empty | catalog (list) |
| controller payload builder `active_skills` tail slot | inject active-skill bodies into the dynamic tail each iteration | active_skills set + bodies |
| `controller_factory.py::is_skills_enabled` + loader wiring | resolve flag (default off); build loader from frozen workspace | env |
| `api/routes.py::GET /v1/skills` + `forced_skills` field | autocomplete list; seed forced skills | loader, flag |
| extension host + composer | `/` autocomplete (prompts + skills), set `forced_skills`, collision rule | `/v1/skills`, `/v1/config` |

Each unit is testable in isolation: the loader against a `tmp_path` skills dir; the prompt block with a
plain manifest list; the tool source with a stub catalog; the budget-rank as a pure function over
manifests + a fake embedder; the route with the in-memory app; the composer with a stub host.

## 5. Error handling

- Loader: every scan/parse/IO error → skip that skill + `logger.warning`; a fully unreadable dir →
  empty catalog. Never raises into a turn (best-effort, same as P1/memory).
- `read_skill`: unknown name / unreadable body → `ToolOutput(is_error=True, ...)` (the model adapts);
  over-cap body → truncated + marker.
- Budget rank: embedder unavailable → degrade to **insertion order / full catalog** (never crash —
  same degrade-not-raise as the memory reranker).
- `forced_skills`: unknown names dropped with a warning; an empty/invalid value is a no-op.
- Script execution inherits `run_command`'s existing failure + shell-policy semantics (no new path).

## 6. Testing

**Python (pytest):**
- Loader: empty dir → `[]`; valid skill → manifest; missing `name`/`description` → skipped + warning;
  `name`≠folder → present + warning; mtime-unchanged → cached object; mtime-moved → re-scan; bad YAML →
  skipped, scan continues.
- Catalog block: appended iff non-empty; absent when `None`/empty; after memory + instructions blocks;
  includes the `run_command` example line. Parametrized like the existing `memory_enabled` tests.
- Budget gate: under cap → full catalog in system prompt, no ranking; over cap → ranked subset in the
  tail, system prompt block omitted; embedder-down → degrade to order.
- `SkillToolSource`: `read_skill` known → body + adds to active set; unknown → error; body cap →
  truncated. `AggregatingToolRegistry` rejects a name collision with a builtin.
- Active-skills tail: a skill activated mid-turn is re-injected on the next iteration; a forced skill is
  present at iteration 1.
- Route: `GET /v1/skills` lists the catalog; gated-empty when flag off. `forced_skills` seeds active set;
  unknown names dropped.
- Factory: `is_skills_enabled` default-off; `1/true` on.

**TypeScript (vitest):**
- `/` autocomplete merges prompt files + skills; skill entry sets `forced_skills` (not inline expand);
  collision → prompt file expansion wins, skill still listed.
- `forced_skills` rides the message payload; `/v1/skills` client mapping (snake→camel).
- `skillsEnabled` `when`-context hides the affordance when off.

**Live smoke:**
1. Drop `.ai-editor/skills/git-commit/SKILL.md` with a distinctive directive; ask a matching question →
   the model calls `read_skill` and the directive demonstrably changes behavior.
2. A skill whose body says to run `scripts/check.sh` → the model emits `run_command` for it and it runs
   through the shell-policy gate.
3. `/git-commit` forced-load → the body is active from turn start without the model choosing it.
4. Add a second skill mid-session → the next turn's catalog includes it (self-updating, no restart).
5. Kill-switch: `AI_EDITOR_SKILLS_ENABLED=0` → no catalog, `read_skill` absent, composer affordance gone.

## 7. Exit criteria

- A project `SKILL.md` is discovered, catalogued, and (via `read_skill`) **demonstrably changes agent
  behavior** on a matching live task; a skill-bundled script runs through the shell gate.
- `/skill-name` deterministically pre-loads a skill (forced-load); `/` autocomplete lists prompts +
  skills with the collision rule.
- A mid-session skill add is picked up on the next turn (self-updating).
- `AI_EDITOR_SKILLS_ENABLED` kill-switch verified (default off; on enables).
- All TS + Python suites + typecheck green; live smoke (1–5) passes.

## 8. Out of scope (deferred)

- **Ecosystem discovery dirs** (`.github/skills`, `.claude/skills`, `.agents/skills`, `~/.agents/skills`)
  — trivial later add (extend the loader's dir list + precedence/collision rule).
- **Embedding-ranked catalog** as a *live* default — wired + tested but dormant until the catalog
  exceeds the char budget; turning it on by default is a follow-up.
- **Dedicated `run_skill_script` tool** — reuse `run_command` in v1; revisit for skill-scoped telemetry
  or external-dir scripts.
- **`disable-model-invocation` / `allowed-tools` / `compatibility` enforcement** — parsed-and-ignored
  (forward-compat) in v1.
- **`context: fork`** (skill-as-isolated-subagent) — this is **P5** (subagents).
- **Cross-turn / thread-scoped active skills** — v1 is turn-scoped.
- **Planning/task-path injection** — dormant path, untouched (same as P1).
- **Skills management UI** (enable/disable/list pane) — **P4**.
- `references/`/`assets/` get no special tooling — reachable via existing `read_file` by relative path.

## 9. Relationship to other phases

- **P1 (done):** reuses the controller prompt-assembly seam + the composer `/`-command path.
- **P3 (MCP):** the budget-gated `Embedder` ranking here is the same Tool-RAG mechanism MCP needs at
  200+ tools — shared infra.
- **P4 (UI):** surfaces skills management + the `/` affordance polish.
- **P5 (subagents):** `context: fork` skills execute in the revived task/subagent path.

## 10. Evidence (real-implementation survey)

Drove the decisions above; full notes in the brainstorming session.

- **agentskills.io spec:** dir `<name>/{SKILL.md, scripts/, references/, assets/}`; required `name`
  (≤64, `[a-z0-9-]`, matches folder) + `description` (≤1024); optional `license`/`compatibility`/
  `metadata`/`allowed-tools`. Progressive disclosure: metadata ~100 tok always → body `<5000 tok` on
  activation → resources on demand via relative paths.
- **Codex:** catalog = name+description+**path**, capped at **8000 chars**; loads full SKILL.md when it
  decides; dirs `.agents/skills` (cwd→root), `~/.agents/skills`, `/etc/codex/skills`, system.
- **opencode native:** single generic `skill({name})` tool; `available_skills` section always injected;
  first-match-wins precedence; permissions (deny→hidden).
- **opencode-agent-skills plugin:** **`use_skill`** tool (body into context, `synthetic:true`+
  `noReply:true` to survive compaction) + **`run_skill_script`** tool; `<available-skills>` catalog at
  init; **semantic-similarity nudge** (not a hard filter).
- **Scale research:** skill catalog entries are cheap (~100 tok) so always-on scales to ~50–100 skills;
  MCP/tool *definitions* are heavy + provider-capped (~128), which is why Tool-RAG (embed descriptions,
  retrieve top-K/turn: 13%→43% accuracy, ½ tokens) is a P3 concern. Sources: Anthropic Agent Skills
  engineering post; Claude/Codex/opencode docs; RAG-MCP (arXiv 2505.03275); Red Hat Tool-RAG.
</content>
</invoke>
