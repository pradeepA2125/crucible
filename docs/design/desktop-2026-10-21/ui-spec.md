# Crucible desktop: UI specification

**Current design:** see `final-ui-design.md` and `collaboration/merged-ui-proposal.md` for the consolidated Codex/Claude direction. This earlier screenshot-based specification remains as design history and detailed state/contract evidence. Its later-scope and explorer descriptions are superseded where the merged direction differs.

**Reuse clarification:** `component-reuse.md` requires direct shared imports of the existing composer/thread/cards. Mockup stand-ins do not authorize replacing those components or reducing their controls.

Companion to `implementation-plan.md`. The interactive proposal uses sample data and simulates UI transitions; it does not connect to a repository or execute commands. Revised after the user's four Codex screenshots on 21 October 2026.

## Design direction

Calm, conversation-first workspace. Reuse Crucible's neutral surfaces, blue accent, monospace code and compact tool activity. Build around controller conversations, enabled agents/teams and accepted edits. Avoid a task-management dashboard or permanent project-wide metrics.

**Current direction: conversation first, with an on-demand tabbed workspace.** This supersedes the earlier recommendation for a persistent three-column inspector. The user's screenshots are the primary visual reference; official desktop material and review documentation provide supporting references.

Three separate surfaces must not be conflated:

1. **Pinned summary:** a small optional card with conversation changes and relevant sources. Its toolbar toggle is independent of the full workspace.
2. **Workspace:** opens to the right of chat, with tabs for Changes and individual files, plus close/expand controls. Opening a file retains the Changes tab. Closing the workspace preserves its tab selection and the conversation draft.
3. **File navigator:** an independently toggled tree at the far right inside the workspace. In Changes it navigates changed files; in a file preview it browses all project files. It does not consume conversation space while the workspace is closed.

The default view keeps the workspace closed. The top-right panel icon opens it; Review and changed-file links open Changes directly. The workspace can expand across the available main area for inspection, then restore the split or close back to chat. This is a proposed Crucible adaptation of the supplied references, not a claim to have inspected the blocked native Codex app.

## Screen anatomy

### Project and conversation sidebar

About 210–240 px on desktop. Brand and new conversation at the top; expandable project roots; recent conversations within each project. Secondary line shows only relevant state: working, needs approval, live agents, interrupted or unread result. Status combines text with a symbol, never color alone. Project name is always visible when navigation changes. Current repository path can be inspected without occupying every row.

Persistent bottom actions: open project, memory, AXON and settings. Runtime progress/failure is a small workspace status entry, expanding into details when needed. A missing repository remains in recents with Locate folder/Remove from recents actions; removal does not delete repository data.

Project switching changes visible context only. Background work remains attached to its workspace. A project with no running backend can show cached recent metadata while preparing the runtime; there is no global automatic startup of every recent project.

### Conversation header

Compact toolbar with project/conversation title, sidebar toggle, pinned-summary toggle and right-workspace toggle. The workspace has its own file tab strip when open. Local stop action appears when the selected conversation has stoppable work. Opening a different conversation never sends Stop. A connection/ownership warning sits beneath the header when relevant and names the affected project.

The header has no task stage selector or mandatory plan workflow. PLAN is an explicit composer choice. It disables controller edits; existing exec-session tool availability and approvals remain as implemented, so it is not a filesystem sandbox. A main-agent response can finish while its background team continues; represent those states separately.

### Transcript

Readable width around 680–780 px, even when the available canvas is wider. User messages, assistant prose, compact activity disclosures and live decision surfaces. Avoid wrapping every paragraph/tool in a raised card.

Reuse the existing transcript distinctions: durable messages; transient thinking/tool activity; exact live gates; team milestones; controller todo ledger. Activity groups summarize reads/searches/commands with expandable evidence. No fabricated percentage progress.

Live gates remain adjacent to the conversation and composer. A main clarify/mode gate explains where to answer; child gates and team plan approvals retain their actual non-blocking or independent behavior. Multiple gates name the originating agent/team and target exact gate IDs. The inspector can show the same gate's details, but it must not create a separate independent approval state.

### Composer

Text with image paste/attachments, @file context and /prompt/skill discovery. The model and effort controls use provider/catalog capabilities, not hardcoded model names or a universal reasoning scale. Context occupancy and thread usage remain separate from account quota.

Controls: model/effort, Plan Mode, confirmed approval mode, context/attachments and Send/Stop according to existing input availability. The prototype's default shows normal ACTIVE mode; no task mode picker. Settings are scoped truthfully to the workspace runtime where applicable. Saved global preferences are not silently represented as independent per-conversation policies.

Draft is per conversation and survives navigation/reload. Render a recoverable admission error without erasing text/attachments. Sending during a user turn follows the actual busy rule; notice-turn queuing retains its existing labeled behavior. Do not add a generic message queue with no backend support.

### Tabbed workspace and file navigator

Closed by default. When opened, share the available main area with chat, around 45–55% for the workspace on a typical desktop. The content remains readable through resizable bounds and expansion; a narrow 285 px permanent inspector is no longer the recommended layout. Top tabs include **Changes** and each opened **file**. Agents and Output can open as auxiliary tabs through existing context actions. File preview is a workspace tab, not a full IDE navigation system.

Controls: toggle workspace from the app toolbar; close individual tabs; browse/open another file; expand workspace; restore split; close workspace; show/hide file navigator. The navigator is at the workspace's **far right** and includes a file filter. Markdown files have rendered preview/source modes; source files open as read-only code. A file tab's path is visible in a compact breadcrumb. Expanded workspace hides the conversation view without cancelling work or destroying its draft. Escape restores split from expanded mode, or returns to chat from split mode. Per-conversation panel state and selected files are restored on navigation.

Changes has an explicit scope label: proposed edit or applied changes from this conversation. File list with add/delete counts; readable unified diff initially, with a later side-by-side option if needed. Already-applied content has no Accept button. Pending edits expose the existing edit gate's Allow edit/Reject action. Acceptance is acknowledged by the backend before the inspector labels it Applied.

V1 does not offer arbitrary per-hunk accept/reject: current decisions apply to the underlying edit gate. Do not show Commit, Stage or Discard all as if the inspector owned a Git transaction. Rewind starts the existing preview/confirmation flow and lists files/limitations before any restoration.

Review must work for completed inline edits. Current `openInlineDiff` depends on temporary shadow paths, which may be unavailable after acceptance. Use captured unified diff/turn edit evidence for historical review; compare against current working-tree bytes only when labeled as current. Never reconstruct history by assuming HEAD is the correct before-version. Specify retention/size caps and stale/missing-preview fallbacks.

Agents shows individual agents and teams with phase, latest result and approval attention. A team expands to board/proposal, members and reports. Deliberate, deliberate-and-implement and implement keep their existing semantics. A discussion-only team cannot acquire an implementation action merely from a decorative UI state. Preserve unresolved objections and interrupted outcomes rather than presenting every ended team as successful.

Output shows existing command sessions and tool results, selected by conversation and session ID. Running/exited/error and truncation are explicit. Start with read-only transcripts; interactive stdin/PTY controls would be additional product scope. A command approval shows exact command, cwd and remembering scope before execution; a successful edit never implies tests passed.

### Setup and settings

First launch: open project → provision runtime components → choose/connect provider → verify capability → open conversation. Reuse existing setup backend and account browser sign-in; do not invent a separate desktop account. Preserve local-model choice and per-component degraded/retry states. Do not ask people to manually install a Python backend.

Settings reuse existing sections: Overview, Appearance, Provider, MCP Servers, Skills, Agents, Instructions, Policies & Memory, Runtime. Default-on features should already be on. Missing MCP configuration is an empty state, not a disabled system. Memory Inspector and AXON have full workspace views reachable from navigation; no new CRUD/memory promises beyond existing capabilities.

The revised prototype includes real local interaction for the workspace toggle, pinned summary, expand/restore/close, tab management, changed/all-file navigation, filtering, rendered Markdown/source, per-conversation draft/view restoration and appearance/accent choices. Its file content is illustrative sample data. Full provider credentials, memory data, AXON rendering, team board and live repository browsing are not implemented in the mockup.

## State inventory

| State | What is visible | Primary action |
|---|---|---|
| No project | Welcome, recent project/open folder | Open project |
| Runtime preparing | Component progress and recoverable failures | Retry failed component / continue degraded where supported |
| Provider missing | Setup/account selection with actual validation status | Connect provider |
| Empty conversation | Project context and useful prompt affordance | Send message |
| ACTIVE working | Activity, relevant todos and Stop | Stop selected work or inspect activity |
| PLAN discussion | Explicit Plan Mode and read-only exploration | Discuss, or implement through actual mode gate |
| Pending edit | Exact files/diff and originating gate | Allow edit / reject |
| Command or MCP approval | Exact command/tool, arguments and remembering scope | Allow once / supported remember / reject |
| Child/team attention | Agent/team attribution without stealing unrelated composer state | Inspect/resolve exact gate |
| Applied edits | Applied label; changed-file evidence; verification status | Review changes / follow up |
| Team still running | Main answer plus live team strip | Open team board |
| Provider limit/sign-in failure | Existing provider-access details | Reconnect/manage usage as supported |
| Disconnected | Cached transcript, reconnect status, unsent draft retained | Retry connection |
| Backend restarted | Interrupted work and current backend identity | Inspect result/start appropriate follow-up |
| Other host controls workspace | Observer indication, actual owning client | Request/take control when allowed |
| Rewind requested | Preview, impacted files, conflicts and side-effect limits | Confirm only when backend permits |

## Keyboard, layout and accessibility

Proposed shortcuts: Cmd/Ctrl+N new conversation, Cmd/Ctrl+O open project, Cmd/Ctrl+, settings, Cmd/Ctrl+Enter send, Escape close preview/dialog. Verify conflicts with native menus and existing composer behavior before shipping. Do not intercept Escape as a hidden destructive Stop command.

Target native app widths 1100–1600 px. The workspace opens on demand at every width. Below about 800 px in the design preview, an open workspace occupies the main area while chat remains available through the close control. At narrow widths the nested navigator reflows under the preview; project navigation is available through its toolbar toggle. Keep project identity and all actions reachable; desktop functionality is not defined by a mobile screenshot.

Keyboard order follows visual order. All icon buttons have labels; active tabs announce selection; resizers have keyboard alternatives; modals restore focus; live updates announce decisions/results without reading every token. Support OS light/dark, text scaling, high contrast and reduced motion. Use semantic status labels and ordinary readable contrast.

## Prototype interaction contract

Local sample interactions let a reviewer switch conversations, start a new conversation, open/close the workspace, expand/restore it, show the pinned summary, navigate Changes and file tabs, filter files, switch Markdown preview/source, inspect agents/output and change the shared Crucible palette/accent. Any counts, filenames and output shown are illustrative sample data. No action invokes a model, changes repository files, opens accounts or runs a command.

## Reference evidence

The four user-supplied screenshots establish the actual visual hierarchy requested: optional pinned summary, separate tabbed review workspace, far-right file navigator and rendered document tab. The local screenshot paths are temporary references, not application dependencies.

Official supporting material: [desktop app](https://learn.chatgpt.com/docs/app), [published desktop video poster](https://learn.chatgpt.com/images/codex/video-posters/data-analysis-fraud-spike.webp), and [review documentation](https://learn.chatgpt.com/docs/code-review?surface=app). The review documentation describes Git-oriented scopes and actions; Crucible V1 remains scoped to its own conversation edit evidence. Adding repository-wide Git staging/committing would be a separate backend/product decision, not an automatic consequence of matching the layout.
