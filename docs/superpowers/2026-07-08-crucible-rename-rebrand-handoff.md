# Handoff — Rename/rebrand "AI Editor" / "ai-editor" / "Shadow Forge" → "Crucible"

**Branch:** `main` (clean, nothing rename-related committed yet)
**Date:** 2026-07-08
**Status:** Scoping done. Zero code changes made. Nothing decided on the 3 hard questions below.

## TL;DR
User wants the product renamed to **Crucible** everywhere — env vars, npm packages,
VS Code extension identity, hidden runtime directories, the GitHub repo, the landing
page, and all docs. A scoping-only research pass (general-purpose agent, this session)
mapped out exactly how big and how risky each surface is. **Three of the seven
surfaces need actual product decisions before any find-replace starts** — those are
listed first since they block everything else. The other four are large but purely
mechanical (script it, then let build/test catch anything missed).

Do not start editing until the three decisions below are resolved with the user.

---

## Repo state right now (context, not related to the rename)
- `main` is at commit with tag **`v0.3.0`** just released and live on GitHub
  (`pradeepA2125/shadow-forge`) — indexer binaries, ripgrep, uv, and (new this
  release) **rust-analyzer** as a 4th managed binary component, plus the memory
  harness now on-by-default. Full pipeline green, VSIX installed locally as
  `ai-editor.ai-editor-vscode-extension@0.3.0`.
- No feature branch is open. `main` is the base for this rename work — recommend
  a fresh branch (e.g. `feat/crucible-rename`) before touching anything, given the
  size.

---

## The 3 decisions that MUST happen before writing any code

### 1. VS Code extension identity — is this a rename or a re-publish?
`apps/vscode-extension/package.json`: `publisher: "ai-editor"`, `name:
"ai-editor-vscode-extension"`, `displayName: "AI Editor"`. The `publisher.name` pair
**is** the Marketplace listing identity — you cannot rename an existing listing's
identifier; changing it means publishing a *new* extension. Existing installs of
`ai-editor.ai-editor-vscode-extension` do **not** auto-migrate to a new
`crucible.crucible` (or whatever) listing.
Also: 24 distinct `aiEditor.*` settings/command keys (`aiEditor.backendBaseUrl`,
`aiEditor.memory.enabled`, `aiEditor.openChat`, etc.), 61 source call sites. Anyone
with `aiEditor.*` entries in their own `settings.json` silently loses that config
if the namespace changes and they install a "different" extension.
**Ask the user:** new Marketplace listing (clean break, old one gets a deprecation
notice pointing at the new one) vs. keep the same publisher/extension id and only
rename the *display* name/branding (`displayName`, icon, README) while leaving
`publisher`/`name`/the `aiEditor.*` settings namespace untouched? The second option
is far less risky and is probably what "rename the branding" actually means in
practice — confirm this explicitly, don't assume.

### 2. GitHub repo name/URL — rename the actual repo, or just the product name?
8 hardcoded refs to `pradeepA2125/shadow-forge` (`install.sh`'s curl URL and
`api.github.com/repos/...` lookup, `.github/workflows/release.yml`'s
`GITHUB_REPOSITORY`-derived URLs — those are dynamic so they self-update on repo
rename — but `README.md`, `landing/src/content.ts`, and
`apps/vscode-extension/package.json`'s `repository` field are hardcoded strings).
GitHub does redirect old URLs after a rename, but anyone with an old cached
`install.sh` pointing at the literal old owner/repo string keeps hitting the old
name (the script itself has a hardcoded default `REPO="${AI_EDITOR_INSTALL_REPO:-pradeepA2125/shadow-forge}"`).
**Ask the user:** actually rename the GitHub repo via `gh repo rename`, or leave the
repo name as `shadow-forge` and only rebrand product-facing text/UI (simpler, zero
external-coordination risk)?

### 3. Hidden per-workspace directories `.ai-editor/` and `.agentd/` — migrate or break?
146 refs/78 files for `.ai-editor/` (skills, prompts, `mcp.json`, AGENTS.md
discovery), 96 refs/53 files for `.agentd/` (shadow workspaces, sqlite DBs,
artifacts, logs) — both scattered as path-literal construction across Python and
TS, not a single constant. Renaming either breaks every existing user workspace:
old shadow state, memory DB, and any user-committed `.ai-editor/skills/` or
`.ai-editor/mcp.json` become invisible to a build that looks for `.crucible/`.
**Ask the user:** ship a one-time migration (detect old dir, rename/copy on first
run) or a documented breaking-upgrade note ("delete `.ai-editor/`/`.agentd/` and
reinitialize"), or don't rename these directories at all (keep `.agentd`/`.ai-editor`
as internal/legacy names, only rebrand user-visible strings)? This is the one most
likely to actually annoy existing users if gotten wrong.

---

## The 4 mechanical surfaces (large, but no decision needed — just execute + verify)

| Surface | Scope | Notes |
|---|---|---|
| **`AI_EDITOR_` env var prefix** | 124 distinct var names, ~1,116 references, 135 files | All literal strings — no dynamic construction found, confirmed by grep. Safe scripted rename. **Real risk is deployment coordination**, not the code: every `.env`, CI secret, and already-deployed backend needs the new names or it silently falls back to defaults on next deploy. |
| **npm/package identifiers** | 6 `package.json` "name" fields (`ai-editor`, `@ai-editor/editor-client`, `ai-editor-vscode-extension`), ~21 import sites across 16 files | **Known footgun, bit us this session:** `apps/vscode-extension/package.json` pins `"@ai-editor/editor-client"` as an **exact version string**, not a range. When renaming the package, the dependent's pin must be bumped/renamed in the same commit or `npm install` 404s against the real npm registry trying to resolve the old name (this exact bug broke the v0.3.0 release CI run before being fixed — see `47d1fe6`). Same class of bug will happen again with a package *rename* unless both sides change atomically. |
| **Landing page branding** | "Shadow Forge" appears 10x across 6 files (`index.html`, `README.md`, `content.ts`, `Hero.tsx`, `Cta.tsx`, `OpenSource.tsx`) of 24 landing files total | Pure copy rewrite — no logo/icon/color-scheme tied to the name, confirmed no visual-identity dependency. |
| **CLAUDE.md + docs** | ~134 matches in CLAUDE.md (766 lines), ~882 across `docs/superpowers/**` (56 files) | Zero functional risk (pure documentation) but too large to hand-edit reliably — script a sed pass, then spot-check for false positives (generic phrases like "the AI editor you're using" vs. the literal product name "AI Editor"). |

---

## Recommended execution order (once the 3 decisions are made)

1. **Branch:** `git checkout -b feat/crucible-rename` off `main`.
2. **Mechanical bulk first** (safe, reversible, build/test catches misses):
   a. CLAUDE.md + docs sed pass, spot-check ambiguous matches.
   b. Landing page copy.
   c. npm package names + import sites — **do this as one atomic commit**, immediately
      run `rm -rf node_modules **/node_modules && npm install && npm run build && npm run test && npm run typecheck`
      from a clean state (exactly the check that would have caught the v0.3.0 CI
      bug locally) before moving on.
   d. `AI_EDITOR_` env var prefix — scripted rename across all 135 files, then full
      Python + TS test suites (expect ~1199 passed / 1 skipped Python, 332 TS —
      those are the current green baselines per this session's `main`; note
      `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies`
      is a **pre-existing, unrelated** failure on `main` already — don't chase it,
      don't let it block this work, it's not caused by the rename).
3. **The 3 hard surfaces**, only after the decisions above are locked in — each is
   its own commit/PR given the external coordination involved (Marketplace
   re-publish, repo rename, workspace-dir migration).
4. Full verification before merging: same 3-suite pass used for the v0.3.0 release
   (`npm run build && npm run test && npm run typecheck`, full `pytest` in
   `services/agentd-py`, `scripts/release` pytest suite) — see CLAUDE.md's
   "Starting the backend for local testing" / testing-patterns sections for exact
   commands and known-flaky-test notes (`test_semantic_index.py` is slow — ~56s,
   not hung — CI's own `pytest-timeout` plugin can falsely appear to hang a fresh
   full-suite run if you add `--timeout=30`; don't bother, just let it run to
   completion or split it out with `--ignore=tests/test_semantic_index.py` +
   run separately, exactly as this session did).

## What NOT to do
- Don't touch `services/agentd-py/agentd/tools/loop.py` or
  `tests/test_command_only_step.py` under this rename — that pre-existing test
  failure is unrelated and out of scope.
- Don't rename anything under `.github/workflows/release.yml`'s
  `${GITHUB_REPOSITORY}`-derived URLs — those are already dynamic and follow the
  repo automatically if/when it's renamed.
- Don't skip the from-scratch `rm -rf node_modules && npm install` check after
  the package-rename step — it's the only thing that reliably catches the
  exact-version-pin class of bug in this monorepo, and it already burned one
  release this session.
