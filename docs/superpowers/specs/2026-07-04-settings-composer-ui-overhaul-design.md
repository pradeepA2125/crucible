# Settings, Setup Wizard & Composer UI Overhaul (Design)

**Date:** 2026-07-04 · **Status:** Approved design, pre-plan
**Roadmap phase:** P4-C of `docs/superpowers/2026-06-29-feature-roadmap-copilot-parity.md` ("UI polish & composer UX"), narrowed to the items below — see §7 for what's explicitly deferred.
**Inputs:** live comparison against GitHub Copilot CLI's "Agent Customizations" settings surface (user-supplied screenshots, `agents_ui/`); two Explore-agent research passes over the current webview-ui (chat composer/theme, settings/setup structure); a visual-companion brainstorm session (`.superpowers/brainstorm/86077-1783187706/`).

---

## 1. Goal

The chat UI is polished (uses the shared `CardShell`/`Btn*` design system); the Settings panel, Setup wizard, and composer are not. Concretely:
- `SettingsApp.tsx` (353 lines) and `SetupApp.tsx` (226 lines) are both raw stacked Tailwind markup that never adopted `CardShell`/`BtnPrimary`/`BtnGhost`/`BtnDanger` — the chat UI's own design system.
- Settings is one long flat scroll with no navigation, unlike Copilot CLI's left-nav sectioned layout.
- There is no way to switch model/provider except the full Settings panel or the one-time Setup wizard — no quick affordance near the composer, despite the model hot-swap route (`PUT /v1/config/provider`) already existing and applying instantly.

This spec turns Settings into a left-nav surface (Copilot-style), reskins the Setup wizard onto the same design system, adds a new Instructions (AGENTS.md) editor section, and adds a model-switch + Settings shortcut to the composer.

## 2. Decisions log (from brainstorm)

| # | Decision |
|---|----------|
| 1 | Settings layout: **left-nav, Copilot-style** (not a light reskin, not an accordion) — dedicated nav rail + an Overview landing page of cards, one section shown at a time. |
| 2 | Nav sections: **Overview, Provider, MCP Servers, Skills, Instructions (new), Policies & Memory, Runtime** — a direct mapping of what exists today plus one new section. No Agents/Hooks/Plugins equivalents (we have no such subsystems). |
| 3 | Instructions section is a **full in-panel editor with Save** (not read-only) — writes `<workspace>/AGENTS.md` directly via the extension host's `fs` access, no new backend route (mirrors `prompt-files.ts`'s direct-fs pattern). |
| 4 | Setup wizard (`SetupApp.tsx`) **is in scope** — reskinned onto `CardShell`/`Btn*` in the same pass, same 4-step flow, no structural change. |
| 5 | Composer quick-bar: **model dropdown (live hot-swap) + a gear icon that opens the full Settings panel.** No inline shell/scope/memory policy chips — investigation found only the model/provider route is a true per-turn hot-swap on the live controller path; shell policy needs a managed restart and scope policy isn't even wired into the controller path (task-subsystem-only, which is flag-gated off). Putting those in the composer as if they were instant would misrepresent how they actually behave, so they stay Settings-only, reached via the gear icon. |
| 6 | Explicitly deferred (§7): memory-inspector palette consistency, `@`-file mentions, unified `/`-autocomplete, and broader onboarding/empty-state polish — kept out to keep this pass focused. |

## 3. Settings panel architecture

`SettingsApp.tsx` becomes a thin shell: a left nav rail listing the seven sections + a content pane rendering whichever is active (plain local `useState<Section>`, no router needed in a webview). Each section is extracted into its own component under `webview-ui/src/settings/sections/`:

- `OverviewSection.tsx` — a card grid (one card per section: icon, title, one-line description, click-to-navigate), matching Copilot's Overview page.
- `ProviderSection.tsx` — backend/model select, model text input, API key input, "Save & validate" (existing behavior, moved).
- `McpSection.tsx` — server list + add-server form (existing behavior, moved).
- `SkillsSection.tsx` — checkbox list (existing behavior, moved).
- `InstructionsSection.tsx` — **new**, see §4.
- `PoliciesSection.tsx` — env-flag dropdowns (existing behavior, moved).
- `RuntimeSection.tsx` — release/version info + restart button (existing behavior, moved).

Every section is rebuilt with `CardShell` for its container(s) and `BtnPrimary`/`BtnGhost`/`BtnDanger` for its actions, replacing the current raw `<button>`/inline-Tailwind approach. Data flow is unchanged: `settings/load` still populates one `SettingsState` snapshot on mount and after each mutating action (per `createSettingsHandler`'s existing "rebuild full snapshot" pattern) — this is a presentation-layer restructure, not a data-flow rewrite. The "restart required" banner behavior (currently top-of-page) stays global, shown regardless of which section is active.

## 4. Instructions section (new)

Purpose: view and edit `<workspace>/AGENTS.md` from within the extension, instead of it being a file the backend silently reads with zero UI.

- **Read path:** `host/settings-data.ts` gains `loadInstructions(workspacePath): { content: string; exists: boolean }`, reading the file directly via Node `fs` (vscode-free, unit-testable — same convention as `prompt-files.ts`'s `loadPromptBody`). No backend involvement; `ProjectInstructionsLoader` on the backend side already mtime-watches this same file independently, so a save here is picked up by the backend automatically on the next turn (no restart, no coordination needed).
- **Write path:** `saveInstructions(workspacePath, content): void`, a direct `fs.writeFile`. Same size-sanity as the backend's own `CRUCIBLE_INSTRUCTIONS_MAX_CHARS` cap is *not* enforced client-side — the backend already truncates-with-warning over budget, so no duplicate validation logic is needed here.
- **UI:** a plain textarea (monospace, full-width) inside a `CardShell`, a `BtnPrimary` "Save" button, and an empty state ("No AGENTS.md yet in this workspace" + a "Create" button that seeds an empty file) when `exists` is false.
- **Out of scope:** no markdown preview, no diffing against the previous save, no undo history beyond VS Code's own file history — this is a plain editor, not a diff review surface.

## 5. Setup wizard reskin

Same `Step` union (`welcome | install | provider | done`) and same linear flow — purely a visual pass. Replace raw Tailwind blocks with `CardShell` for each step's content area and `Btn*` for actions (Next/Back/Retry/Open chat). The per-component install-progress icons (⏳/✓/✗/⤷) are preserved as-is; only their container styling changes.

## 6. Composer quick-bar

`InputArea.tsx`'s footer row gains two new elements at the **left** of the existing row (abort buttons / spacer / Review-each-step checkbox / ⌘↵ hint / Send button all stay, unchanged, to the right):

- **Model dropdown** — shows the current `backend`/`model` (fetched once via the existing settings data source). The popover lists only providers that already have a stored API key (i.e. already validated once via Settings) — picking one calls the existing `PUT /v1/config/provider` route directly, applying from the next turn per the route's documented "no restart" hot-swap semantics. A provider with no stored key is **not** offered in this list; picking a brand-new provider (with its key entry + validation) stays a Settings-panel action, reached via the gear icon. This avoids the composer ever attempting a hot-swap that's guaranteed to fail validation.
- **Gear icon** — a plain icon button that runs the existing `aiEditor.openSettingsPanel` command. No new backend surface; this is a shortcut, not a new settings mechanism.

## 7. Deferred (recorded, not designed here)

Kept out of this pass, per the roadmap's original P4-C list and the brainstorm's explicit scope decision:
- Memory-inspector's hardcoded slate palette → theme-adaptive.
- `@`-file mentions in the composer (clickable, feeds turn context).
- Unified `/`-autocomplete dropdown (prompts + skills, badged).
- Broader onboarding-walkthrough / empty-state / error-surface polish beyond the two surfaces named in §3/§5.

## 8. Testing

- **Unit (TS):** one test file per new/split section component (mirrors existing `gates.test.tsx` / `settings-data.test.ts` patterns) — Overview navigation, each section's render + action wiring, `loadInstructions`/`saveInstructions` (happy path, missing-file empty state, write failure), composer model-dropdown selection calling the hot-swap route, gear icon invoking the command.
- **Live smoke (dev host):** navigate all 7 Settings sections; create AGENTS.md from empty state, edit, save, confirm the backend picks it up next turn with no restart; switch model from the composer and confirm the next response uses it; click the gear icon and confirm Settings opens; visual pass over the reskinned Setup wizard end-to-end.

## 9. Risks / open questions

- **Section-splitting churn:** moving five existing sections into new files is mechanical but touches every current Settings test — expect those tests to move/rename alongside their components, not just new tests to appear.
- **AGENTS.md concurrent-edit:** if a user edits AGENTS.md in a normal editor tab *and* the Settings panel simultaneously, last-write-wins with no conflict detection — acceptable for v1 (same class of risk as any two editors on one file), not designed around here.
- **Model dropdown data source:** needs the same provider list `ProviderSection.tsx` already has (`PROVIDERS` in `settings/types.ts`) — reuse it rather than duplicating, so the composer and Settings never show divergent provider lists.
