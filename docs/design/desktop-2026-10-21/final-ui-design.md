# Crucible desktop — merged UI direction

21 October 2026. This is the current consolidated design, combining Codex's screenshot-based shell with Claude Code's desktop workflow features. The app is not implemented. The interactive preview uses local sample data, simulated decisions and a simulated shell.

**Latest user clarification — direct component reuse:** mount the existing React composer (`InputArea`), thread/transcript (`ThreadView` / `TranscriptItems` / `MessageRow`) and complete live cards (`LiveSlot` and gate components). Do not rebuild them from the HTML mockup. New UI work belongs to the desktop shell and workspace containers; shared components gain small host-neutral extension points only where required. The existing component's appearance and full behavior take precedence over the mockup's stand-ins. See `component-reuse.md` for the source audit and reuse matrix.

Claude Code independently confirmed this in `collaboration/reuse-review-claude.md`. Use a desktop shell root around the shared ThreadView/store/providers and existing root-level confirmation dialog; retain VS Code's current App entry. Agent/team content can gain inline panel wrappers. Memory/AXON retain separate document/bridge boundaries because their current API acquisition and messages are independent.

The subsequent full reuse review is recorded in `collaboration/reuse-followup-claude.md`. Reuse also includes the existing history row/status presentation and extracted host message/model wiring. The new sidebar owns cross-project navigation/attention metadata; chat continues through the existing controller and one active UI store. Required host behavior includes external-link/drop navigation handling, clipboard support, shortcut priority and update-safe assets. Ownership enforcement and safe controller attachment remain explicit new shared integration work. See the updated reuse contract for the full boundary.

## The window

The initial view is a wide centered conversation with a compact title bar, project/conversation sidebar and the existing Crucible composer. The right workspace is closed until opened. A small optional pinned summary is separate from the workspace. The summary's sources derive from actual tool activity in the product; the preview labels its sources as samples.

The top-right panel icon opens the workspace beside chat. Its tabs include Changes and individual files. The + menu opens Changes, Browse files, Terminal or Agents. A separate folder icon toggles the navigator at the far right inside the workspace. Expand uses the main area for inspection; restore brings chat back; closing the panel retains the draft and tab state. Navigation never means Stop.

## Features taken from both proposals

| Area | Final direction |
|---|---|
| Sidebar | New conversation, Search, Waiting for you when needed, Pinned, collapsible project groups, status/unread markers, conversation actions, Archive and existing Memory/AXON/Settings entry points. Every conversation has a project. |
| Attention | Selecting a waiting row opens that conversation's exact gate. Decisions remain in the conversation; background badges do not create a second approval state. |
| Search | Cmd/Ctrl+K palette for conversations, files and actions. Path search first; content search is separately bounded host work. |
| Files | A discoverable Files landing tab plus individual file tabs. Landing includes changed/recent files and the navigator. Markdown preview/source; source wrap, path copy and Open in editor. |
| Explorer | Whole workspace visible: hidden/ignored entries and empty folders included. Lazy per-directory expansion; ignored entries dimmed/collapsed. Filtering has a separate Include ignored choice. Internal data, binary/oversize previews and outside-root links have explicit limits. |
| Changes | Captured Conversation edits; retained checkpoint → current-file comparison with explicit naming; read-only Uncommitted vs HEAD. Viewed state, file navigation and review comments attached to the next draft. Already-applied edits have no Accept action. |
| Terminal | Your shell and agent output are clearly distinct session types. User PTY is an added desktop feature; exited agent sessions have no Stop action. The preview executes nothing. |
| Agents | Existing agent/team inspection in workspace tabs, discussion vs implementation semantics preserved. Team approval remains tied to its actual conversation/card. |
| Composer | Reuse existing model/effort, approval, Plan Mode, context occupancy, attachments, skills/prompts and Send/Stop controls. Provider capabilities drive choices. Relevant settings apply at their actual project/global scope, not independently per conversation. The preview illustrates only a subset. |
| Background work | Notifications for relevant background attention/completion, quiet tool activity, configurable keep-awake, ownership-aware close/quit with running turns/agents/teams/command sessions. |
| Appearance | Shared Crucible Dark/Light and Blue/Violet/Ember, plus desktop Follow system. No separate desktop palette or agent engine. |

## Runtime and feature decisions

- Use Electron with a trusted host process and a narrow versioned bridge. Renderer has no backend token or direct backend connection. Share React/chat/theme/controller/runtime code with the extension through host adapters.
- Build on the current reactive controller and shipped managed defaults. Tasks remain off; no Tasks tab or mandatory plan/execute/final-promote workflow.
- V1 supports one controlling app per canonical project, with an explicit notice if another app owns it. No silent backend borrowing, automatic takeover or two independent writers. Correctness needs startup serialization, compatible ownership admission and supported-version handling; a lock-file label alone is not enforcement. Shared leases/lifetime are later capabilities.
- Initially admit one new user main turn per repository, while keeping existing team/sub-agent behavior. Safe conversation switching needs shared attachment/hydration states and integration checks before the extension's current navigation lock is removed.
- Persisted history, a live renderer reconnect and a backend restart are different states. Execution is detached from the stream while its backend lives; it is not durable restart continuation. Reconcile persisted history and live state around the bounded replay buffer.
- Historical captured diffs and checkpoint-to-current comparisons are distinct. Checkpoints are retained/capped and do not cover arbitrary command writes. Unavailable evidence stays unavailable; HEAD is only the explicit Git baseline.
- Include read-only Git comparison in the early inspector phase. Backend-mediated per-file restore is added review scope with project-wide busy/write coordination, displayed-content validation and recovery. External writes cannot be fully excluded. Stage/commit/push and per-hunk mutations remain later.

Detailed decisions and source checks are in `collaboration/merged-ui-proposal.md`, `codex-round-1.md` and Claude's reports. These decisions supersede conflicting scope/coexistence statements in the earlier two proposals; the originals are preserved.

## Implementation sequence

1. Shared host/theme/bridge and a real chat/setup/edit/approval vertical slice.
2. Shell, attention/pins/unread/archive, path palette, complete read-only explorer, honest Changes/Git scopes, Agents/output, safe navigation and explicit ownership.
3. User PTY terminals and lifecycle/native packaging; notifications, quit/close/keep-awake use reliable liveness, including command sessions.
4. Backend-mediated restore and recovery. Stage/commit/push stay a separate later milestone.

The earlier supported-macOS estimate of 7–10 weeks is a planning baseline for the earlier scope, not a promise for every added feature. PTY/restore/native packaging require spike-based sizing. Signing/update access affects distribution, but does not block this UI design review.

## Review record

Claude's original session reviewed our docs and its own proposal in round 1, reviewed the merged direction in round 2, and reviewed the finished candidate/screenshots in round 3. Its verdict is **Ready to show Pradeep as the merged design proposal**, with no open design disagreements. Codex fixed the remaining fidelity issues (gate/composer behavior, tab overflow, quit emphasis and related labels) and validated them locally. Each report retains its author's identity and distinguishes exact snapshot review from subsequent corrections. See `collaboration/codex-final.md` and the original reports for the outcome; this document is not user approval to implement or publish.

## Workspace launcher amendment (21 October)
The latest user direction, captured in collaboration/workspace-launcher-pradeep.md, supersedes the initial Files/Agents landing. The right workspace opens a real New tab page listing implemented Tools. Choosing Files or Agents replaces that launcher tab with the selected tool and gives it the whole body. Plus adds another New tab; closing the final tab returns to a launcher. Files and Agents are singleton tools; individual files remain separate tabs. The file navigator and its toggle are shown only in Files/file previews. Suggested lists real recent files and observed applied edits, hidden when empty. No placeholder tools or shortcut hints; Changes arrives CP5, shortcuts CP7, Terminal CP10. Existing shared composer/thread/cards stay reused.
