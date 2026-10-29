# Source map and scope

Assessment date: 21 October 2026. Repository revision at assessment start: `6bd7dc65246db6f818f35d18b3fcce71b4ebfe5a`.

`CLAUDE.md` is the engineering instruction source. Its reactive-controller section explicitly supersedes the legacy ChatAgent flow; task documentation earlier in the file describes an optional subsystem, off in the managed product. Current defaults were verified against actual environment construction and flag resolvers, rather than assuming every historical paragraph describes the shipped path.

| Responsibility | Source anchor | What it establishes |
|---|---|---|
| Current product model | `CLAUDE.md:178–190` | ACTIVE/PLAN, accepted-edit promotion, gate IDs, frozen workspace and task subsystem off |
| Trace discipline | `CLAUDE.md:844–855` | Follow caller → callee → values at the boundary |
| Managed defaults | `apps/vscode-extension/src/runtime/backend-process.ts:127–186` | Controller on; tasks off; enabled managed features and workspace-local paths |
| Backend flag resolution | `services/agentd-py/agentd/chat/controller_factory.py:20–112` | Bare-process versus managed defaults, dependency relationships |
| Controller selection | `services/agentd-py/agentd/chat/controller_factory.py:145` onward | Existing controller, memory, instructions, skills and MCP wiring |
| Current React entry | `apps/vscode-extension/webview-ui/src/App.tsx:8–53` | History/thread views and navigation lock |
| Host-neutral controller ports | `apps/vscode-extension/src/controller.ts:53–193` | Settings/UI/diff/client interfaces |
| Active-view state | `apps/vscode-extension/src/controller.ts:193–245` | Single active thread, stream, timers and context state |
| Thread switching | `apps/vscode-extension/src/controller.ts:788–825` | Rehydrate transcript, reset live view and subscriptions |
| UI bridge | `apps/vscode-extension/webview-ui/src/sharedVscodeApi.ts:1–22` | Current acquireVsCodeApi dependency and no-op fallback |
| Shared React state | `apps/vscode-extension/webview-ui/src/hooks/useAppState.ts` | Reducer, message listener, authoritative view updates |
| Controller conversation | `services/agentd-py/agentd/api/routes.py:1684–1755` | Turn launch, per-thread in-flight guard and queue special case |
| Policy scope | `services/agentd-py/agentd/chat/controller.py:1514–1565` | Process/version policy CAS, global waiting-edit resolution, plan preference |
| Existing capabilities | `services/agentd-py/agentd/api/routes.py:284–354` | Actual `/config` payload for UI gating |
| Gate APIs and team views | `services/agentd-py/agentd/api/routes.py:1824–2249` | Mode/clarify/edit/command/MCP/team decisions, agents/teams, usage and rewind |
| Backend auth | `services/agentd-py/agentd/auth.py:1–8, 145–195` | Loopback, exact Host, no Origin, token boundary |
| Existing HTTP/SSE client | `apps/editor-client/src/client/http-backend-client.ts:172` onward | Typed backend access and streaming parser |
| Replay limit | `services/agentd-py/agentd/orchestrator/broadcaster.py:1–65` | 50-event volatile replay; no durable replay promise |
| Runtime discovery/start | `apps/vscode-extension/src/runtime/backend-process.ts:197–351` | Lock proof, reuse and spawn |
| Runtime shutdown | `apps/vscode-extension/src/runtime/backend-process.ts:450–473` | Only tracked child handles killed by stop |
| Host startup/shutdown | `apps/vscode-extension/src/runtime/vscode-runtime.ts:376–405, 495–506, 562–572` | Settings-based start, restart and disposal |
| Lock persistence | `services/agentd-py/agentd/runtime_lock.py:1–57` | Atomic lock write; discovery data rather than startup exclusion |
| Installer core | `apps/vscode-extension/src/runtime/installer.ts:1–38, 185–203` | Dependency-injected installer; external Node/npm requirement |
| Supported runtime architectures | `apps/vscode-extension/src/runtime/manifest.ts:1–53` | Four supported platform keys and checksum model |
| Backend persistence | `services/agentd-py/agentd/main.py:64–74, 311–316` | Workspace-relative task/chat databases and startup-frozen workspace |
| Chat schema | `services/agentd-py/agentd/chat/storage.py:1–175` | Chats, controller state, checkpoints, sub-agent/team storage |
| Settings/credential adapter | `apps/vscode-extension/src/settings-deps.ts`, `runtime/vscode-runtime.ts:226–356` | Host config and VS Code secret handling |
| Subscription auth adapters | `apps/vscode-extension/src/chatgpt-deps.ts`, `claude-deps.ts` | Browser launch, registrations and backend account APIs |
| Account token storage | `services/agentd-py/agentd/chatgpt_auth/store.py:100–179` | Backend-managed registrations and refresh coordination |
| File navigation | `apps/vscode-extension/src/file-references/{catalog,matching,open-reference,workspace-adapter}.ts` | Reusable matching with a VS Code inventory adapter |
| Existing diff UI | `apps/vscode-extension/webview-ui/src/components/shared/DiffPanes.tsx`, `src/review-diff.ts` | Capped unified diff versus VS Code native diff |
| Existing feature entries | `apps/vscode-extension/webview-ui/vite.config.ts:12–22` | Chat, memory, graph, setup and settings bundles |
| Indexer supervision | `apps/vscode-extension/src/runtime/backend-process.ts:354–415` | Watcher process and backend notification environment |
| Indexer backend notification | `services/indexer-rs/src/backend_auth.rs:99–125`, `src/service.rs:353–360` | Authenticated index-build notification |
| Existing release pipeline | `.github/workflows/release.yml` | Tests, runtime asset matrix, manifest, wheel and VSIX packaging |

Counts from this checkout: 53 non-test host TypeScript files, 14 with direct VS Code imports (including type-only imports), and 174 non-test React/UI TypeScript files with no direct VS Code module import. Counts are a boundary indicator, not a reuse percentage: message APIs, CSS variables, focus/lifecycle assumptions and editor services still require adaptation.

Uncommitted paths observed at start: three UI layout tests modified, a new `chromeLayout.ts` helper, and `.vscode/`. They were not changed or used as evidence for committed architecture. Generated proposal files are separate from those edits.

Limits: no provider calls, full runtime execution, full test suite, signing, installer run, native Electron validation or performance benchmark was performed. The UI proposal uses labeled sample data and local interactions. The current-architecture diagram is repository-backed; target architecture in the implementation plan is explicitly proposed.
