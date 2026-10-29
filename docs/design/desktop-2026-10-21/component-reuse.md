# Desktop component reuse contract

21 October 2026. Pradeep clarified that the approved new design concerns the desktop shell: reuse the existing VS Code React composer, thread/transcript and cards directly. Do not implement the simplified HTML mockup's chat/composer/cards as a second product UI. This clarification takes precedence over any mockup detail or earlier wording that suggests replacement controls.

## Source audit

The actual composition is `src/main.tsx` → `FileReferenceProvider` + `App` → `useAppState` + `ThreadView` → `TranscriptItems`, `LiveSlot`, `InputArea`, team/agent contexts and windows. Paths in this document are relative to `apps/vscode-extension/webview-ui/` unless qualified.

`src/sharedVscodeApi.ts:11` acquires a window-cached message API. Outside its host it supplies a no-op stub. `src/vscodeApi.ts:11` exports that handle under the historical name `vscode`; this is not an import of VS Code's extension runtime. `useAppState.ts:556` receives host messages and announces `webviewReady`. Composer/gates send typed messages through this handle. A desktop adapter must implement those messages; the no-op stub does not make the UI functional by itself.

A static import scan of 171 production TypeScript/TSX modules under this UI source found no direct imports of `vscode`, `electron` or `node:*`. This is evidence about the UI module boundary, not proof that its complete host protocol is already implemented for desktop. Source inspection traced the actual message bridge and its consumers as described here.

## Reuse matrix

| Existing source | Desktop decision | Integration work |
|---|---|---|
| `components/InputArea.tsx` | Reuse the exact composer component and its dependencies. Preserve look, layout, input/image handling, recovery and keyboard behavior. | Supply existing props/messages and add bounded per-conversation draft/attachment storage without replacing the composer. |
| `ModelMenu.tsx`, `EffortSlider.tsx`, `ApprovalMenu.tsx`, `ContextMeter.tsx`, `TriggerDropdown.tsx`, slash/mention/usage helpers | Direct shared imports. No parallel model/approval/context controls. | Desktop host supplies actual catalogs, provider capability descriptors, confirmed policy/context state, prompt/skill/file inventories. |
| `components/HistoryView.tsx` row/status presentation | Export/extract the existing module-private `ThreadRow` and `StatusChip` for reuse in the desktop sidebar. | New project grouping, attention, pins/unread/archive and row actions wrap the shared presentation. Keep existing status semantics and navigation guards; no second chat store. |
| `components/ThreadView.tsx`, `shared/TranscriptItems.tsx`, `MessageRow.tsx`, activity and message components | Reuse the existing chat surface and rendering/grouping logic. | Small shared navigation/header slots or props allow the desktop sidebar/title bar to supply navigation. Keep scroll, stream grouping and live-state handling in the shared source. |
| `components/LiveSlot.tsx`, `inputAvailability.ts` | Reuse gate dispatch, attribution and composer availability. | Continue exact thread/gate identities; workspace inspection never creates an independent approval decision owner. |
| `messages/gates/EditGate.tsx` | Reuse edit/accept/reject card unchanged. | Preserve gate ID, reason, resolved state and backend acknowledgement. Already-applied edits remain distinct from pending approval. |
| `messages/gates/CommandGate.tsx` | Reuse complete command card unchanged. | Preserve exact/prefix/binary rule selection, prefix length, command quoting, rule preview, Allow once / Allow and remember / Reject and one-shot handling. Do not port the simplified mockup dropdown. |
| `McpGate.tsx`, `ClarifyGate.tsx`, `ModeGate.tsx`, `TeamPlanGate.tsx` | Direct reuse. | Preserve each gate's actual semantics, feedback, agent/team attribution and Plan Mode flow. Legacy task-only gate components remain dormant with tasks off. |
| `messages/TodoCard.tsx`, `WorkBar.tsx`, `TeamStrip.tsx`, session/tool/activity components | Reuse. | Background navigation changes routing and selected context, not the components' meaning. No Tasks tab or duplicate progress engine. |
| `shared/DiffPanes.tsx`, `messages/DiffCard.tsx` and formatting helpers | Reuse supported diff rendering and inline cards. | New Changes workspace supplies explicit scope/data and historical evidence. Extending viewport/navigation is shared work; new Git sources are host work. Do not force an unsuitable diff format through an existing component. |
| `agents/AgentTranscript.tsx`, roster/status components, `teams/MemberView.tsx`, Journey/phase/workflow/usage components | Reuse content, data, subscriptions and actions. | Mount in a new workspace tab through the existing providers; no second team/agent view model. |
| `AgentWindow.tsx`, `TeamWindow.tsx` | Reuse their content/logic; adapt presentation if embedded. | Today they include absolute scrims, dialogs, close/Escape/focus behavior. Add a neutral presentation option or extract shared body/header while preserving the original modal wrapper for VS Code. Don't insert an unchanged scrim/modal into an inline workspace tab. |
| `SettingsApp.tsx`, existing sections, `SetupApp.tsx`, account/provider subcomponents | Reuse screens/forms/validation behavior. | Desktop host implements existing settings/setup actions. Add desktop-only preferences in shared sections where appropriate; don't rewrite provider/account/MCP/skills forms. |
| `MemoryApp.tsx`, `GraphApp.tsx` (AXON) | Reuse existing entry/view components in their own documents. | Separate BrowserWindow or isolated sandboxed frame in a workspace tab, each with its own bridge. They acquire the API directly and use unprefixed messages; do not mount them in the chat React tree unchanged. A desktop graph renderer is not new scope. |
| `RewindDialog.tsx`, `FullAccessFirstSendDialog.tsx`, notices, primitives, icons and `styles/themes.css` / `index.css` | Reuse. | Shared dialog lifecycle and theme build; desktop system/high-contrast appearance adapter and actual trust/scope semantics. |
| `hooks/useAppState.ts`, message types, input availability, context/usage, agent/team and transcript reducers/helpers | Shared state/behavior, one implementation. | Versioned project/thread routing and hydration at the host boundary; avoid two simultaneous independent reducers governing one conversation. |

## Broader reuse beyond the component list

The component matrix above was not an exhaustive limit. Reuse extends through UI behavior, shared host logic, the typed client, runtime provisioning and the existing Python/Rust services. Absence of a direct VS Code import is an extraction candidate, not proof a module can be moved without tracing its dependencies.

| Layer | Existing code to share | Desktop-specific boundary |
|---|---|---|
| UI primitives/content | Markdown rendering, file references, image lightbox, copy/file rows, thinking/tool details, notices, buttons/icons, popover/focus/scroll helpers, graph HUD/layout/scene, memory presentation | Shared styles/providers and document boundaries; native clipboard/editor opening where required |
| UI behavior/state | Transcript grouping, activity summaries, status wording, slash/mention parsing, effort descriptors, context/token formatting, agent/team state/event merging, journey/chapter/identity logic, input availability | Selected-project/thread routing and per-conversation retained drafts; do not copy these algorithms into the shell |
| Host-neutral controller | `src/controller.ts`, approval coordinator, agent/team view managers, composer model orchestration, context state, session storage, prompt/instruction/mention/review helpers | UI/settings/secure-store ports; one controller instance per open project, with explicit stream detach/attach, cancellation and hydration behavior. The existing controller owns one project/active thread; it is Node code, not renderer code |
| Host message routing/wiring | Extract reusable validation, intent dispatch, ready-state replay, error recovery and model/effort coordination from `src/chat-panel.ts:286–557`, `:715–724` and `src/extension.ts:107–330` | Keep VS Code panel APIs in their adapter; add the desktop IPC/project envelope around shared routing. Preserve pending Full access questions, model epochs, credentials in the trusted host, context limits and clamped effort persistence. |
| Settings/setup/account logic | `settings-data.ts`, `settings-sections.ts`, `setup-data.ts`, `memory-data.ts`, ChatGPT/Claude account settings logic | Replace VS Code dependency providers, browser/native callbacks, configuration and SecretStorage adapters; no automatic secret migration claim |
| File/graph data logic | `file-references/{catalog,matching,bridge,open-reference}.ts`, `graph/{space-model,snapshot-store,graph-messages}.ts` | Desktop inventory/watch/editor adapters, canonical root checks and per-document bridge; existing file inventory is not a whole-workspace explorer by itself |
| Backend client/contracts | `apps/editor-client`: typed HTTP/SSE client, schemas, request/response contracts and wire parsing; backend health/auth gate logic | Trusted desktop host supplies authenticated transport and routes renderer messages; do not expose bearer tokens to the renderer |
| Runtime core | Installer/archive/manifest/checksum, health probes, backend token/process supervision, JDTLS and platform command helpers in `src/runtime/` | New desktop runtime wrapper for settings/secrets/status/lifetime; implement the proposed ownership/startup/version safeguards in the shared runtime and preserve external executable requirements. Existing authenticated backend discovery/reuse is not controlling-client ownership enforcement |
| Services | Existing Python reactive controller, providers/accounts, tools/MCP/skills, memory, approvals, edit/rewind, agent/team and command-session services; existing Rust indexing/retrieval | Lifecycle and explicit API/behavior changes from the merged plan: new startup reservation/single-instance enforcement, controlling-client admission for mutations and project-wide restore coordination. These are new safeguards, not already shipped guarantees. No second agent engine, memory system, provider layer or indexer. Tasks stay off. |
| Verification/assets | Existing component/reducer/controller/approval/runtime/client/backend/indexer suites and runtime release assets | Run shared suites against the extracted core plus new desktop bridge/native/lifecycle integration checks. Existing test files do not prove desktop integration alone. Add explicit shipped-default coverage: the Python suite fixtures normally force sub-agents, teams and capability loading off, so relevant integration checks must opt them back on. |

The native runtime wrapper (`runtime/vscode-runtime.ts`), VS Code panel management, activation/commands, VS Code configuration/secrets/session-store adapters and native diff opening are adapters to replace or split. Their underlying controller/data/runtime behavior is not automatically new work. New shell controls should reuse existing primitives, status/formatting helpers and authoritative state too.

## New desktop shell and integration work

- Native window/title bar and left project/conversation sidebar, attention/pins/unread/archive, palette and project navigation.
- Right workspace container: tab strip, summary toggle, file navigator, file preview routing, Changes scope/navigation and resize/expand/restore behavior. Reuse the existing content/renderers inside it wherever suitable.
- Native filesystem enumeration/editor launching/clipboard ports, native PTY shell infrastructure and terminal surface, notification/keep-awake/quit integration.
- Electron main/preload and desktop transport/runtime adapters around extracted shared host routing. Keep credentials and backend networking out of the renderer.

These shell pieces use the existing tokens/primitives and consume the same authoritative state. Their existence is not a reason to copy or redesign the composer, transcript, cards, settings or agent/team content.

## Minimal composition plan

0. Extract existing host message routing and composer model/effort wiring into shared host code behind ports before adding the desktop IPC envelope. Preserve validation, replay/recovery and epoch behavior; do not re-derive these rules in a second router.
1. Keep the existing webview source and build pipeline as the common UI. Initially desktop can import from its current location; physical relocation to a shared UI package is optional and follows working integration, not a rewrite prerequisite.
2. Install the desktop message adapter before any module initializes the `vscode` singleton. Preserve the existing typed UI protocol behind a validated project/thread-aware host envelope. Use one React tree/providers where surfaces share context; preserve the one-acquisition rule.
3. Add a desktop shell root that mounts the existing `ThreadView` and one instance of `useAppState`. Keep VS Code's `App`/HistoryView as its current entry. Give ThreadView a narrow `chrome="panel" | "embedded"` presentation option (panel default) and required header/action hooks; desktop supplies its sidebar/title bar. Avoid CSS hiding of arbitrary internal DOM or a forked DesktopInputArea/DesktopGate family. Mount the existing `FullAccessFirstSendDialog` at the desktop root too, outside thread selection, just as App does today: importing ThreadView alone omits this important guard.
4. Retain `ThreadView`'s InputArea, TranscriptItems, LiveSlot, team strip and settings/rewind/trust dialogs as real components. Host file/diff/open-view actions route into the desktop workspace.
5. Extract shared agent/team content from modal presentation only as needed. Existing AgentWindow/TeamWindow remain wrappers over that same content. Desktop tabs use equivalent subscriptions and cleanup.
6. Add per-conversation draft/attachment persistence and safe detach/hydration before enabling free navigation during work. Validate both hosts at each seam; reuse does not make these existing limitations disappear.

## Known host/layout dependencies

- `theme.ts:32` resolves VS Code body classes and `themeChanged` messages; `index.css:63` uses font variables with fallbacks. Reuse the token stylesheet and adapt appearance resolution to desktop system/user preferences. Don't add a competing desktop theme.
- `ThreadView.tsx:48` owns a local draft; InputArea's thread-change effect (`:45`) aborts image readers, clears attachments and calls `onDraftChange("")`. Per-thread retained drafts/attachments are new shared state integration, not already guaranteed by direct mounting.
- `App.tsx:12` and `ThreadView.tsx:132` lock navigation while a turn is active; keep the shared controller attachment/hydration audit from the merged plan. Do not remove the lock as a cosmetic change.
- ThreadView owns subscriptions and contexts (`:73–113`) and closes agent/team selection on thread change. A tab container must keep these scoped and release them on close/switch.
- Existing chat expects a bounded flex viewport with scrollable messages and retained bottom composer. Supply that container rather than adopting the growing illustration's height model.
- `memory/vscodeApi.ts` and `graph/vscodeApi.ts` each call `acquireVsCodeApi()` directly. Their unprefixed `ready`, `refresh`, `error` and other messages overlap. Reuse them with one bus per document; namespace/refactor only if same-document embedding becomes an explicit requirement. The graph host keeps authenticated fetch in the trusted process (`extension.ts:823–825`), rather than granting it to the renderer.
- Task-named internal props/protocol names do not require enabling the task subsystem. Keep controller defaults and task UI off as agreed.

## Desktop host behavior required for shared UI

- Keep app documents loaded when ordinary Markdown links or file drops request navigation. Route validated external web links to the browser and file references through the existing workspace action; deny unexpected navigation/windows. Cover both normal navigation and new-window events, and verify dropped-file behavior in the chosen Electron version. Drop-to-attach is a separate optional shared feature; it is not currently implemented by InputArea.
- Preserve existing clipboard behavior. CopyButton uses `navigator.clipboard.writeText` and visibly reports failure. If the desktop uses a custom app scheme, configure its standard/secure capabilities as required and verify clipboard availability in the shipped build; use a narrow native clipboard port if necessary. This is not a reason to rewrite copy controls or bypass CSP.
- Define shortcut priority: active dialogs, popovers and composer bindings own their keys before shell shortcuts. Add shared focus-aware handling where necessary; do not steal Escape or existing input combinations.
- The existing Vite build emits stable asset names (`assets/[name].js`). Give the desktop build hashed/versioned asset URLs or explicit cache invalidation so updates cannot reuse stale bundles; do not impose an unverified caching assumption on the custom protocol.

These requirements follow the source audit and Electron's [navigation/window guidance](https://www.electronjs.org/docs/latest/tutorial/security/) and [custom protocol capabilities](https://www.electronjs.org/docs/latest/api/protocol). They are host integration work, not replacements for the React UI.

## Preview status

The current unified HTML preview is a **shell/layout illustration**. Its hand-written composer, cards, sample model controls and agent content are stand-ins. They are explicitly superseded by this direct-reuse contract and must not be used as production component implementations or as authority to remove existing features. The next implementation-fidelity preview should mount the actual React components inside the new shell, with a local fixture host if needed.

## Peer confirmation and bounded changes

The original Claude Code session independently reviewed the source and wrote `collaboration/reuse-review-claude.md`. Its verdict: **production mounts the existing React components; the unified HTML is a shell/layout illustration**. It specifically says the real CommandGate is the specification, not the preview dropdown. Codex verified its Memory/AXON acquisition/message findings against source and incorporated them above.

Agreed shared UI extension points:

1. ThreadView panel/embedded chrome and header actions.
2. Agent/team opening through their existing contexts, overridden to open workspace tabs and included in subscription tracking.
3. A bounded per-conversation draft/attachment store, with existing VS Code behavior retained as the default until shared navigation work is validated.
4. Agent/team content separated from modal chrome, with the same content in both hosts.
5. Desktop system-appearance resolution feeding the existing tokens.
6. Export/extract the existing HistoryView row/status presentation for sidebar reuse, with existing behavior retained.

This is a named, bounded integration list, not permission to redesign the shared controls. Further changes need a concrete coupling reason and shared behavior validation; they are not automatic forks or a new user approval requirement.

Codex additionally verified that a new shell root must retain the existing root-level FullAccessFirstSendDialog (App.tsx mounts it outside both views), FileReferenceProvider, theme initialization and single-acquisition behavior. The live stores and confirmations remain shared rather than being re-created in the shell.

No production UI files were changed for this audit. The actual React-in-shell preview is the appropriate next implementation-fidelity artifact; this audit does not claim the current HTML stand-ins have been converted into React.

## Latest peer follow-up

At Pradeep's request, the original Claude Code session reviewed the fuller reuse discussion in `collaboration/reuse-followup-claude.md`. Codex verified the new source findings and incorporated shared history rows, host-router/model-wiring extraction, desktop navigation/clipboard/shortcut/asset handling and corrected ownership/controller/service wording. The shell keeps a small cross-project navigation/attention store alongside the single active chat store; it does not introduce a second conversation engine. `collaboration/reuse-followup-response-codex.md` records the synthesis and qualifications. This remains a design contract, not a completed extraction or desktop implementation.
