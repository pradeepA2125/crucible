# Crucible Desktop — standalone app design

Date: 2026-10-21 · Status: draft for review
Mockups (approved): `.superpowers/brainstorm/23204-1792559987/content/full-app-v2.html` (whole window) and `side-panel-v2.html` (side panel detail). Earlier screens in the same folder are superseded.

## 1. Intent

Crucible today lives inside VS Code. A standalone desktop app makes it usable as an agent workspace of its own, in the shape people already know from the Codex desktop app and the Claude desktop Code tab: a sidebar of threads, the chat in the middle, a composer, and a side panel for changes, files and terminals. The VS Code extension stays and keeps working; both products are built from the same code so neither drifts.

## 2. Goals, non-goals, anti-goals

Goals
- A macOS desktop app with layout B: thread sidebar · chat · on-demand side panel (Changes, Files, Terminal, Agents).
- Reuse the existing React chat UI, theme tokens, composer, cards and gates **unchanged in look**. Design work is limited to the new shell parts: sidebar, title bar, side panel, palette, dialogs.
- One window serving every project the user opens.
- Adopted from the Claude desktop app: Waiting for you, pins/unread/archive, terminal, command palette, notifications, keep-awake, quit warning.
- Both products ship from one shared host layer; a change in shared logic lands in both.

Non-goals (v1)
- Editing code in the app. Files are read-only; "Open in editor" hands off to the user's editor.
- Windows and Linux builds (planned later; nothing in the design should block them).
- Browser/preview pane, split view, worktree per thread, PR/CI pane, side chat, remote control, SSH hosts, cloud sessions, dictation, global quick-entry hotkey.
- A Tasks/Plan tab: the todo card and Plan Mode cards already live in the chat and stay there.
- Generated commit messages (the Commit form requires a typed message in v1).
- Any backend behaviour change beyond the three read-only additions in §8.

Anti-goals (would make this a failure even if it runs)
- The two products diverging: logic copied into the desktop app instead of shared.
- A second visual language. The desktop app must look like the extension's chat, not a re-theme.
- A renderer with Node access, or any path that sends the backend token to the renderer.
- Losing work silently: quitting, closing a project or reverting from the Changes tab without the same safeguards the extension has.

## 3. Constraints from the codebase

These come from reading the code on 2026-10-21 (`main` at `6bd7dc65`).

1. **One backend serves one workspace.** The controller path is frozen at backend startup (CLAUDE.md "workspace-frozen"). A window with several projects means one backend process per open project.
2. **The webview UI already speaks a pure message protocol.** `webview-ui` talks to its host only through `acquireVsCodeApi().postMessage` (`src/sharedVscodeApi.ts`) and `window` `message` events. No VS Code API is imported. It runs in Electron if the preload provides the same two things.
3. **`CrucibleController` (`src/controller.ts`, 2660 lines) is VS Code-free but bound to one project**: it reads one backend URL (`SettingsProvider.getBackendBaseUrl`) and one workspace (`ControllerUI.getWorkspacePath`), and owns one active thread and its `/live` poll.
4. **VS Code coupling is ~3.5k lines in 14 files**:
   - `extension.ts` (1041): activation, ~25 closures handed to `ChatPanel`, commands, status, MCP QuickPicks, approval wiring.
   - `chat-panel.ts` (826): a message router of ~45 `type` cases, each calling an injected handler, plus HTML/CSP building.
   - `runtime/vscode-runtime.ts` (629): `RuntimeManager` — status bar items, `globalState`, `SecretStorage`, `getConfiguration`, error modals, around the VS Code-free `BackendProcess`/`RuntimeInstaller`.
   - Smaller: `settings.ts`, `settings-deps.ts`, `settings-panel.ts`, `setup-panel.ts`, `memory-panel.ts`, `graph-panel.ts`, `review-diff.ts` (uses `vscode.diff`), `vscode-session-store.ts`, `chatgpt-deps.ts`, `claude-deps.ts`, `file-references/workspace-adapter.ts`.
5. **Backend auth** (CLAUDE.md "Backend authentication"): every request needs the per-port token; `BackendGate` probes `/health` before sending one. Requests carrying an `Origin` header are refused, so the renderer can never call the backend directly — the main process must.
6. **Lockfile reuse**: `BackendProcess` reuses a backend that proves it serves the workspace. If VS Code already runs a backend for project X, the desktop app reuses it rather than starting a second.
7. **Dialogs are React, not host modals** (existing rule; `FullAccessFirstSendDialog` already follows it).
8. **Memory cost per backend**: with memory and the reranker on (the defaults), each backend loads the embedder and cross-encoder models. Several idle backends are expensive; see §6.4.

## 4. Architecture

### 4.1 Packages

```
apps/
  editor-client/        unchanged
  host-core/            NEW  @crucible/host-core — everything VS Code-free that both hosts run
  vscode-extension/     thin VS Code adapter over host-core (+ webview-ui, unchanged location)
  desktop/              NEW  Electron app: main, preload, shell renderer
```

`host-core` receives, moved as-is (with their tests): `controller.ts`, `approval-mode.ts`, `agent-views.ts`, `team-views.ts`, `context-usage-state.ts`, `task-poller.ts`, `session-store.ts`, `composer-models.ts`, `prompt-files.ts`, `mentioned-files.ts`, `review-files.ts`, `settings-data.ts`, `settings-sections.ts`, `setup-data.ts`, `memory-data.ts`, `chatgpt-settings.ts`, `claude-settings.ts`, `instructions-file.ts`, `theme-settings.ts`, `backend-auth/*`, `file-references/{catalog,matching,bridge,open-reference}.ts`, and `runtime/*` except `vscode-runtime.ts`.

New in `host-core`:
- `HostPlatform` interface (§4.2).
- `ChatMessageRouter`: the ~45-case switch now in `ChatPanel.registerHandlers`, taking a `post(msg)` function and a handler object. `ChatPanel` keeps only panel lifecycle and HTML building.
- `RuntimeManagerCore`: `RuntimeManager` minus status bar, `globalState`, `secrets` and modals, which it reaches through `HostPlatform`.
- `wireController(platform, …)`: the composer-model, effort, provider-credential and catalog-cache closures now inline in `extension.ts` (lines ~107–330), so both hosts build the same handler set.

A guard test fails the build if any file under `apps/host-core/src` imports `vscode` or `electron` (same style as `tests/test_scripts_use_auth_helper.py`).

`webview-ui` stays where it is. The desktop build consumes its `dist/` output; host differences reach it as data, never as forks (§4.4).

### 4.2 `HostPlatform`

Derived from every VS Code call the host files make today:

| Capability | VS Code impl | Desktop impl |
|---|---|---|
| `globalState` / `workspaceState` get/update | `context.globalState` / `workspaceState` | JSON store in `app.getPath("userData")`, workspace keys namespaced by project path |
| `secrets` get/store/delete | `context.secrets` | `safeStorage`-encrypted file in userData |
| `config` typed reads + change events (the `crucible.*` keys) | `getConfiguration` | same keys in a settings JSON, edited from Settings |
| `notify(level, text, actions?)` → chosen action | `show*Message` | in-app toast; system `Notification` when the window is unfocused |
| `openFile(path, range?)` | `showTextDocument` + reveal | open in the side panel Files tab |
| `openInEditor(path, line?)` | `showTextDocument` | the user's chosen editor (`code`, `cursor`, `zed`… via a setting; falls back to `shell.openPath`) |
| `openDiff(entry)` | `vscode.diff` | open the Changes tab at that file |
| `openExternal(url)` | `env.openExternal` | `shell.openExternal` |
| `listWorkspaceFiles()` | `findFiles` | `rg --files` with the bundled ripgrep |
| `isTrusted()` / `onTrustGranted` | workspace trust | a per-project trust prompt on first open (React dialog), stored in the JSON store |
| `backendStatus(state)` | status bar items | title-bar indicator + sidebar project row |
| `log(channel, line)` | output channels | rotating log file in userData/logs, "Show logs" menu item |
| `navigate(view, arg?)` | `executeCommand("crucible.openSettingsPanel" …)` | route change in the shell |

QuickPick/InputBox uses (the task-path prompts and the MCP add/list commands) stay VS Code-only. The desktop app reaches the same features through the existing Settings › MCP Servers UI; the task path is off by default.

### 4.3 Desktop process model

- **Main process = the host.** It owns a `ProjectRegistry` (open + recent projects, persisted) and, per open project, a `ProjectRuntime`: its `BackendProcess` (through `RuntimeManagerCore`), its `CrucibleController`, a `BackendGate`/client factory, and an attention poller.
- **One `BrowserWindow`** loads the shell renderer from a custom `crucible://app/` protocol (not `file://`). `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`, the same CSP the webview uses (plus `worker-src` for Monaco).
- **Preload** exposes exactly: `acquireVsCodeApi()` → `{ postMessage, getState, setState }`, and dispatches main→renderer messages as `window` `message` events. That is all the existing webview code needs. It also exposes a small, typed `crucibleShell` channel for shell-only messages (sidebar, side panel, palette, terminal I/O).
- **Active project routing.** Chat messages from the renderer go to the active project's controller. Selecting a thread in another project makes that project active: its controller's UI sink becomes live, the previous one's becomes a no-op (it stops its `/live` poll but keeps attention polling), and the renderer gets the new thread through the existing `switchChatThread` flow. My call: one active controller at a time, not N live chat views — that matches one chat column on screen.
- Memory inspector and graph open as separate `BrowserWindow`s with their existing bundles (`memory.html`, `graph.html`).

### 4.4 Host differences reach the webview as data

On `webviewReady`, the host posts `{type:"hostInfo", kind:"vscode"|"desktop"}`. The webview uses it only for:
- hiding its own thread-list view and back button (the desktop sidebar replaces `HistoryView`);
- labelling "Open in editor" vs "Open file" affordances.

No other forks. New shell components (sidebar, side panel, palette) live in a new `webview-ui/src/shell/` entry (`shell.html`) that **embeds** the existing `App` for the chat column, so they share one React tree, one theme, one message bridge.

## 5. UI design

Use the existing theme tokens (`styles/themes.css`), primitives and components. No new colours. Mockup files above are the visual reference.

### 5.1 Window
macOS hidden-inset title bar. Three columns: sidebar (resizable, collapsible, ⌘B), chat (fills), side panel (hidden by default, resizable, width persisted per window, ⌘⌥B; can go full screen within the window).

### 5.2 Sidebar
Top to bottom:
- **＋ New thread** (⌘N) in the active project; **Search** opens the palette (⌘K).
- **Waiting for you**: every thread blocked on a gate in any running project, with the gate kind as a badge (edit, command, MCP, clarify, mode, team plan). Click opens the thread at its card. Hidden when empty.
- **Pinned** threads (any project).
- **One group per project** (collapsible, named by folder): threads newest first, with the existing status dot (running / review / done / failed), relative time, bold when unread. Hover menu: pin, archive, rename, delete.
- **＋ Open project…** (folder picker, then the trust prompt on first open).
- Footer: Settings, Archive (archived threads view), current model.

Unread = thread `updated_at` later than the user last viewed it (host-local). Pins, archive and last-viewed are host-local state, not backend state.

### 5.3 Title bar
Thread title · project and branch chip · diff-stat chip (`+123 −8`, the active thread's changes; click opens Changes) · side-panel toggle · backend status indicator (only shown when not healthy).

### 5.4 Chat column
The existing chat, unchanged: transcript, activity groups, cards, gates, todo card, team strip, composer with approval mode, model + effort, Plan Mode, context meter. Files referenced in the chat (changed-file rows, `@` mentions, file links) open in the side panel instead of an editor.

### 5.5 Side panel
A tab strip (rebuilt from Codex's design: `thread.sidePanel.*`). Tabs close individually; ＋ opens a menu of: Changes · Files · Terminal · Agents (running sub-agents and each live team). Tabs are per thread; the panel remembers the open tabs for each thread.

**Changes**
- Header: source picker — **Last turn** · **This thread** · **Uncommitted** · **Staged** · **Unstaged** · **Branch → base**. Git sources appear only in a git repo. Default: Last turn.
- Files toggle shows a changed-files tree with "Filter changed files".
- Per file: collapse, Viewed checkbox (host-local), Open in editor, Revert file. Per hunk: Revert; in git sources also Stage/Unstage. Header: Revert all, Stage all (git), Commit.
- Inline comment on a line: the comment is attached to the composer as quoted context (`path:line` + text) and sent with the user's next message; shown as a pending chip above the composer until sent.
- Commit form: message (required in v1), Commit / Commit and push. Uses the user's git.
- Large diffs: one file at a time with next/previous, like Codex.
- Safeguards: revert actions need confirmation (with "Don't ask again"); all revert/stage actions are disabled while **any** thread in that project has a turn running, since another thread may be editing the same file.
- Revert is a host-side write of the before content to disk. It does not go through the backend's rewind (which also truncates the transcript); it is the same as the user editing the file by hand, which the backend already tolerates.
- "Last turn" and "This thread" compare the content before the turn with the file **as it is now**, so later hand edits show up too. The header says so ("before this turn → now"). The turn's own end state is not stored and is not needed for v1.

**Files**
Workspace tree (respects `.gitignore`, via `rg --files`) with "Filter files…", breadcrumb, read-only viewer with syntax highlighting, word wrap toggle, copy path, Open in editor.

**Terminal**
User shells: a real PTY per tab, cwd = project root, named after the running process. Agent command sessions appear as tabs of their own (read-only output + Stop), fed from the existing session data on `/live` and the transcript route.

**Agents**
The existing `AgentWindow` and `TeamWindow` content, rendered as tabs instead of floating windows (desktop only; the extension keeps floating windows).

### 5.6 Command palette (⌘K)
One list, three sections: Threads (all projects), Files (name match, plus content match through ripgrep), Commands (new thread, toggle panels, open settings, open project, new terminal, restart backend, switch model…).

### 5.7 Other surfaces
- **First run**: the existing setup wizard (`setup.html`) in the window, then "Open project…".
- **Empty state** (no project open): Open project and a recent-projects list.
- **Settings**: the existing `SettingsApp` as a full-window page, opened from the sidebar footer and the palette.
- **Notifications**: system notification when a thread in the background starts waiting or finishes, and the window is unfocused. Dock badge = waiting count.
- **Quit warning**: React dialog when any project has a turn running, agents running or a live team: "Crucible is working in N threads and M teams. Quitting stops them…". Same on closing a project.
- **Keep awake** (setting, default on): `powerSaveBlocker` while any work runs.
- **Keyboard**: ⌘N new thread, ⌘K palette, ⌘B sidebar, ⌘⌥B side panel, ⌃` new terminal, ⌘[ / ⌘] previous / next thread, ⇧⌘T reopen closed thread, ⌘, settings.

## 6. Data and behaviour

### 6.1 Thread lists across projects
Each project's threads come from its own backend (`GET /v1/chat/threads?workspace=`, which already returns `updated_at` and `agents_running`). Its `status` comes only from a thread's task, so it is `null` for every controller thread while the task subsystem is off; the running/finished state comes from the `turn_active` addition in §8. The host caches the last list per project on disk so the sidebar renders immediately at launch, then refreshes when that project's backend is up.

### 6.2 Backends start lazily
A project's backend starts when the user opens one of its threads (or creates one). Starting the app does not start every backend.

### 6.3 Waiting for you
Polls `GET /v1/chat/attention` on each running backend (the extension already polls it every 10 s). Only running backends can have live gates, since a restart reaps turns. Needs the gate kinds — see §8.

### 6.4 Backend cap
To bound memory, at most 3 project backends run at once (setting). When a fourth is needed, the least recently used backend with no running turn, agents or live team is stopped. If none is idle, the user is told which projects are busy instead of stopping work. Stopping uses the existing graceful path.

### 6.5 Changes sources
- **Last turn / This thread**: from the backend's rewind checkpoints, which already hold each touched file's pre-turn content (`chat/rewind.py`). Before = checkpoint content (first-seen-wins across the span, as `restore` folds it); after = the file on disk. Reverting writes the before content back. Needs a read route — see §8.
- **Git sources**: computed by the host with the user's `git` (`diff`, `diff --cached`, `diff <base>...`). Stage/unstage hunks via `git apply --cached [-R]`.
- Diffs are computed in the host from full file contents, so the 400-line `unified_diff` cap on chat diff entries does not apply here.

### 6.6 Leaving a thread while it runs
Today the extension locks thread switching while input is disabled (`App.tsx` `navLocked`). The desktop app must allow switching freely (Waiting for you, ⌘[ / ⌘], the palette). Backend turns are already detached and durable, and the controller already re-subscribes to a running turn on reload (live-resume), so switching away detaches the controller from the old thread's stream and switching back reuses the live-resume path. This changes `host-core`, so the extension gets it too; the composer stays locked while its own thread's turn runs. My call: ship it to both products in Phase 2, with tests for switch-away / switch-back / gate-on-other-thread.

### 6.7 Terminal
`node-pty` in the main process, `@xterm/xterm` in the renderer, bytes over the `crucibleShell` channel. Shells die with the app and are not restored in v1.

## 7. Keeping both products in step

- All behaviour lives in `host-core`; the two adapters stay thin. The guard test (§4.1) enforces it.
- Webview changes are tested once and ship to both. Desktop-only components live under `webview-ui/src/shell/`.
- CI builds and tests both on every PR: `npm run build`, `npm run test`, `npm run typecheck` already run across workspaces; the desktop app joins the same scripts.
- The managed runtime (`~/.crucible/runtime`) is shared. Both apps use the same installer and manifest; installs take an install lock so the two apps never install at the same time (`installer.ts` has none today — add it).

## 8. Backend additions (read-only, additive)

1. `GET /v1/chat/threads/{id}/changes?scope=turn|thread` → per file `{path, before_exists, before_text | before_binary, oversize}` from the rewind checkpoints (latest checkpoint for `turn`, the whole span for `thread`). Add it to the reviewed list in `tests/test_get_routes_read_only.py`.
2. `GET /v1/chat/attention` gains `gate_kinds: string[]` per thread (kinds only, never payloads — the route's existing rule).
3. `GET /v1/chat/threads` and `GET /v1/chat/attention` gain `turn_active: bool` and `last_turn_ended_at`, so the host knows which threads are running or just finished without polling `/live` for every thread. The sidebar dots, "finished" notifications, quit/close warnings, keep-awake and the backend cap's idle check all use it. Attention also lists threads whose only activity is a running turn.

Everything else the desktop app needs already exists.

## 9. Packaging (macOS v1)

electron-builder: `.dmg` + `.zip`, arm64 and x64. Hardened runtime, notarization (`notarytool`), Developer ID signing. Auto-update with electron-updater from GitHub Releases. `node-pty` is rebuilt for Electron's ABI. The release workflow (`.github/workflows/release.yml`) gains a macOS desktop job. Runtime install reuses the existing manifest and installer. The installer uses the system `npm` for pyright and typescript-language-server and skips them without Node; the desktop app accepts that degraded mode in v1 and says so in Settings › Runtime ("Install Node.js for full code-graph edges") rather than bundling Node.

## 10. Phases

Each phase ends with both products working.

| Phase | Delivers | Rough size |
|---|---|---|
| 0 · Shared host layer | `host-core`, `HostPlatform`, `ChatMessageRouter`, `RuntimeManagerCore`, `wireController`; extension rewired; guard test. No user-visible change | ~1 week |
| 1 · Window | Electron app: preload bridge, one project, existing chat running, managed backend, setup wizard, Settings page, logs | ~1 week |
| 2 · Shell | Sidebar (projects, threads, waiting, pins, unread, archive), multi-project routing, lazy backends + cap, title bar, notifications, quit warning, keep-awake, `hostInfo` | ~1 week |
| 3 · Side panel | Tab strip, Changes (all sources, revert, stage, comments, commit), Files, the two backend additions | ~1.5–2 weeks |
| 4 · Terminal, Agents, palette | PTY terminals + agent session tabs, Agents tab, ⌘K palette, keyboard map | ~1 week |
| 5 · Ship | Signing, notarization, auto-update, release job, install lock, end-to-end smoke | ~1 week |

Total ~6.5–7 weeks. That is above the earlier 4–6 week estimate because the side panel (git-backed Changes, Files) and terminal are now in scope.

## 11. Verification

- `host-core`: the existing controller/runtime/settings vitest suites move with the code and must stay green.
- Desktop main logic (project registry, routing, backend cap, Changes computation, git staging) is vscode/electron-free where possible and unit-tested in vitest.
- End-to-end: Playwright's Electron driver launches the app against a real managed backend and drives open project → send message → edit gate → Changes revert → terminal → quit warning.
- Each phase is live-checked in the built app, not only in tests.

## 12. Risks

- **Memory with several backends** — mitigated by lazy start and the cap (§6.4); measure per-backend RSS in Phase 2 and tune the default.
- **Native modules** (`node-pty`) complicate signing and notarization — prove the signed build in Phase 1, not Phase 5.
- **Controller assumptions about a single window/project** may surface during Phase 2 routing (timers, `attention` listeners, `contextState`) — keep inactive controllers' UI sinks inert and test switching.
- **Monaco or a lighter viewer** (left to the builder, §13) affects bundle size and CSP.

## 13. Left to the builder

- Diff/viewer component: Monaco (diff editor + read-only viewer) vs extending `DiffPanes` with a syntax highlighter. Either is fine if it matches the theme tokens.
- Exact JSON store library (or a hand-rolled file).
- Whether `host-core` source moves physically in one commit or file by file, as long as each commit is green.
- Toast component design, using existing `.surface-card` patterns.

## 14. Calls made during design (for review)

- One window for all projects; one active controller at a time; lazy backends; cap of 3.
- Terminal is a side-panel tab, not a bottom drawer.
- "This thread" Changes source built from rewind checkpoints, alongside git sources.
- Files tab is read-only; editing hands off to the user's editor.
- Agents and teams become side-panel tabs in the desktop app only.
- Electron (not Tauri): the host code needs Node.
- macOS first; Windows/Linux later.
- Commit messages are typed, not generated, in v1.
- A third read-only backend addition (`turn_active`, `last_turn_ended_at`) instead of polling `/live` per thread.
- Switching threads while a turn runs is allowed, in both products.
- Changes-tab revert is a host-side write, guarded project-wide; Last turn compares against the file as it is now.
- No bundled Node: without it, the LSP servers are skipped and Settings says so.
