# Desktop implementation status

21 October 2026. Implementation started after Pradeep approved the pair-programming workflow. Codex drives; original Claude Code independently reviews each frozen source patch. **Checkpoint zero is complete and independently closed by Claude Code.** **Checkpoint 1 is complete and independently closed by Claude Code**, including the final provider credential boundary fix. It delivers a runnable one-project Electron development host. **Checkpoint 2 is complete and independently CLOSED by Claude Code** (collaboration/cp2-closure-review-claude.md), including final90-file approval-chain verification and independent reproduction of all2134 tests.

## Delivered shared foundation substeps

- **0a: chat dispatcher/contracts.** Existing dispatch behavior is shared through typed action/view ports. VS Code retains its platform adapters. Claude: no blockers; normalized source parity verified.
- **0b: model/provider coordinator.** Existing catalogs, epoch/registration invalidation, model/context swaps and applied effort persistence are shared behind runtime/client ports. Historic provider-name export preserved. Claude: no blockers; normalized algorithm parity and activation timing verified.
- **0c: retained view/projection.** Existing outbound chat projection, pending Full access questions, context/workbar/approval replay and model-result lifetime checks are shared in ChatView, with ChatPanel as the native adapter. Claude: no blockers; moved method payload parity verified.

The existing React UI was not rewritten or changed. No backend defaults changed; reactive behavior remains current and tasks stay off. No commits or pushes. Main checkout's pre-existing UI-test edits were preserved.

## Validation and exact review scope

**0d completes the remaining source foundation:** the optional task-only action port and shared runtime core/native adapter split. Reactive diff opening remains a required core action. Preference/secret keys, settings-to-env mapping, crash/restart behavior and shipped defaults are preserved. Claude reviewed the final 22-file source snapshot independently: both initial blockers fixed, no remaining source blockers (`cp0d-fixes-review-claude.md`).

Full production build including webview, typecheck and diff check pass. Final suites: editor-client169, extension732, React1157 — **2058 passed**, one unchanged perf skip. An overloaded concurrent React run was interrupted after timeouts; its log is preserved. React then passed at two workers. Claude independently reproduced all final suites sequentially. See `collaboration/cp0d-validation.md`.

Real managed-backend/provider VS Code acceptance completed in an isolated development profile and disposable workspace. Completed checks include real turns/tool activity, edit rejection, command once/reject/remembered narrow prefix, webview reload replay, sub-agent roster/window, model/effort persistence across owned backend restart, live appearance, restart-required settings, and Plan Mode across threads and window reload. All applicable required acceptance checks are complete, and Claude independently judged A1–A7 closed in `collaboration/cp0-closure-review-claude.md`. MCP was N/A (no configured server), team was explicitly optional/skipped, and the selected supported effort required no clamp. Exact evidence is in `collaboration/cp0d-live-smoke.md`.

Worktree: `/Users/pradeepkumar/.codex/worktrees/crucible-desktop-foundation/AI editor`.
Branch: `codex/crucible-desktop-foundation`.
Base: `6bd7dc65246db6f818f35d18b3fcce71b4ebfe5a`.
Current file hashes: `collaboration/cp0-current-snapshot.json`.
Original frozen snapshots, full patches, handoffs and Claude-authored reviews are in `collaboration/`.

## Next checkpoints and recorded follow-ups

Checkpoint 0 is closed. Source extraction and native acceptance are complete and independently reviewed. The pre-existing adopted-backend restart no-op and absence of a crash watcher for adopted backends are deliberately preserved, pinned by a regression, and recorded for a later ownership fix.
Checkpoint 1 implements the Electron development host, scoped validated bridge and real shared UI/backend. Full root checks passed at2089 tests plus one existing skip; the final two-file desktop-only credential fix then passed desktop typecheck/build and35 tests (2093 tests across the final checked sources). Claude independently verified the final34-file snapshot and reproduced the final desktop checks. Real native acceptance and VS Code regression are recorded in collaboration/cp1-live/acceptance.md; final closure is in collaboration/cp1-closure-review-claude.md. Checkpoint 2 adds the approved sidebar/workspace shell. Safe multi-project view/persistence isolation, ownership enforcement, full explorer/Changes, user PTY and distribution remain sequenced work under the agreed design. Run `npm run desktop:dev` from the foundation worktree with Node22.12+; see apps/desktop/README.md. The approved desktop shell is checkpoint 2.

## Explicit carry-forward findings

The existing first-send Full access + webview reload/navigation path can silently lose an unsent draft: the question replays, then the controller drops the now-stale send without restoring the draft. Claude confirmed it is pre-existing (controller/React unchanged), recommends a separate fix before or with checkpoint2, and did not block foundation closure. Normal Continue, Switch to Accept edits and Cancel were independently smoke-tested. The adopted-backend restart no-op also remains a separately tracked ownership fix.

Claude also verified supplemental full AX/PNG evidence37 for the restart-required banner; the final closure addendum confirms that the last evidence gap is closed. All applicable required checks pass.

## Checkpoint 1 closure and carry forward

Claude independently closed all17 scope items, then rechecked the final credential allowlist fix and confirmed CLOSED. The existing React App/composer/cards/setup/settings remain reused, without React source changes. Native acceptance covered real provider turns, edit and command gates, Stop, reload, model/effort/settings/Plan Mode persistence, Rewind, sub-agent Stop, mentions, clipboard, isolation, restart, shutdown, live-client refusal, and VS Code regression. Temporary desktop remembered-command rules were removed; all test-owned app/backend instances are stopped. Nothing committed or pushed.

Checkpoint2 starts with the approved desktop sidebar/workspace shell and draftStore. Before expanding it, review per-key settings/setEnvFlag values and verify untrusted approval policy precedence over shell environment flags; add desktop read-path filtering for obsolete stored credential names. Record the threat model: the first-party renderer retains intended user powers such as MCP server configuration, agent/instruction edits and pending gate decisions; the bridge does not sandbox those intended capabilities. New provider extraCredentials are now limited to the selected provider's declared fields in both preload and main.

Carry forward the previously accepted ownership limits (including conservative live-orphan refusal and reverse adoption), provisional preference scope before multi-project support, Full access/trust UI, and a physical Finder drag check. Unsent drafts can still be lost on Settings navigation until draftStore lands. Full explorer/Changes/PTY and distribution remain later scoped work; this checkpoint is a single-project development host requiring an existing runtime.


## Checkpoint 2 final implementation

Complete and independently CLOSED by original Claude Code; see collaboration/cp2-closure-review-claude.md. Every final source hash traces to an approved slice, and Claude independently reproduced all four typechecks and2134 passing tests plus one existing skip. Codex remains sole source writer; no commits/pushes. The standalone entry adds the one-project sidebar/title filtering and right inspection workspace around the existing ThreadView, InputArea, cards, contexts, settings and providers. VS Code retains its default presentation. The workspace uses the actual AgentWindow/TeamWindow bodies and one shared subscription owner, with expand/restore/hide/close controls and an approval-attention route back to hidden chat. Failed initial live-status loading leaves navigation usable and exposes Retry. Sidebar liveness is additive and truthful; missing old-server fields remain unknown.

Bounded per-project/thread draft storage in main memory retains text/images/mentions across reload, full-document Settings and idle switches. Separate current/returned slots preserve newer edits and pre-admission refusals. A pending submission is pinned; asynchronous flush precedes navigation/reload/quit. No disk draft persistence is claimed. Native Full access remains unavailable until the trust checkpoint; real store/adapter/controller tests cover its stale first-send path. Desktop per-key env flags and obsolete stored extra credentials are constrained, preserving shipped defaults.

Final checks:172client +740extension +1173UI +49desktop =2134 passed, one existing UI perf skip, all root types and builds pass. Native desktop/provider and VS Code regression evidence: collaboration/cp2-live/acceptance.md. Exact90-file source snapshot: collaboration/cp2-final-snapshot.json. All test-owned app/backend instances stopped.

The earlier Settings draft-loss and desktop stale first-send findings are resolved by CP2's draft protocol. Continue carrying the accepted ownership/reverse-adoption/live-orphan limitations, provisional preference scope, explicit desktop trust/runtime installation, and the physical Finder drag check. Full explorer/Changes/user PTY, multi-project ownership and distribution remain subsequent scoped checkpoints.


## Full UI completion roadmap and CP3 in progress

Pradeep asked for all remaining checkpoints before proceeding sequentially and highlighted the fixed left pane. The agreed complete coverage/sequence is checkpoints.md (3–13), independently scope-approved with amendments in collaboration/cp3-roadmap-review-claude.md. CP2 remains a closed shell slice, not full approved UI completion.

The original Claude review channel recovered after Pradeep said “try again”. CP3a host and renderer, CP3b dynamic right split, and CP3c native pointer-release correction are independently APPROVED. Current source frozen in collaboration/cp3-final-snapshot.json. Final monorepo test run:172 client +740 extension +1186 UI +51 desktop =2149 passed,1 existing UI performance skip; all typechecks and renderer/desktop/extension builds pass. Native desktop acceptance verifies both dividers, keyboard bounds/reset/steps, hide/show, per-project collapse, Show more/less, search beyond8, active-row retention, 720px compact/Restore, saved312/588 widths on reload/relaunch, retained draft/inspection. VS Code native regression passes; original Claude CLOSED CP3 in collaboration/cp3-closure-review-claude.md. CP4a is next. No commits/pushes. Pradeep chose encrypted persistent drafts OFF by default for9.

## CP4 progress

4a0 filesystem core,4a1a local ignore rules incl case detection,4a1b path filter/IPC incl project-scope cancellation,4b0 real renderer independently APPROVED.4a1c raster host approved with dimension-limit follow-up;4b0 focus-refresh S1 follow-up in progress. Existing composer and ThreadView remain reused; actual Files tabs/tree now connected. FullUI1190passed+1existing skip before latest small follow-ups; latestdesktop88passed beforedimension follow-up. No nativeCP4 acceptance/closure claim yet. Later4c Markdown/actions/invalidation/nativecontextmenu and4a1c pixellimits remain tofinish. No commits/pushes.
