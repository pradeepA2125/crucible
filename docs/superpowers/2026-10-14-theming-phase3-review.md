# Theming Phase 3 review

14 October 2026. Reviewed the complete Phase 3 change against baseline `beceda3` and revision 6 of the approved design, before integrating it onto `feat/codex-style-chat-theming`. This was a separate whole-diff review pass by Codex, followed by corrections and regression checks; no independent reviewer or sub-agent was used.

## Result

No outstanding blocking findings. Composer controls, backend ownership/versioning, client wire mapping, host migration and serialized writes, all four readiness/admission paths, fallback capabilities and modal focus were reviewed together. Protected/untrusted edits, remembered command rules, child definitions, MCP approval and task Stop-and-revert preserve their existing exceptions and behavior. Nothing was pushed.

## Findings corrected

- Submitted mode, clarification and team-plan cards could remain answered after failed admission. A scoped host retry message now reopens the affected card while retaining its draft. Message failures restore text/images/mentions and remove the optimistic bubble.
- Disabling the native effort slider during provider validation lost keyboard focus. It returns to the initiating control after acknowledgement or failure. Arrow, Home and End were exercised in the browser.
- Restarting into an older backend could leave a previously supported mode looking confirmed. Live process changes and capability changes now trigger a fresh config read; older backends show their unconfirmed window setting without replaying auto-accept. Backend URL changes also invalidate confirmation.
- A successful backend write followed by failed preference persistence could be reported as not applied. The confirmed backend state now remains authoritative, with a specific persistence error.
- Escape could reopen a hover-only context card through programmatic focus. That path now dismisses without refocusing and reopening the ring.
- The composer referred to an undefined focus-border token. It now uses the existing focus-ring token. Modal focus filtering also excludes controls inside CSS-hidden ancestors.
- The UI audit added semantic usage links, image dimensions, shared visible keyboard focus, and wrapping for long provider/approval/image errors. Removed the obsolete separate effort menu and decorative WorkBar hairline.
- Mode/clarification failures after navigation now respect the captured thread/backend context before changing input state.

## Validation

- Full JavaScript suites: 157 editor-client, 545 extension, and 1,064 webview tests passed. The existing opt-in stream performance test remains skipped.
- Full monorepo typecheck and build passed.
- Backend controller/chat/review/sub-agent/notice/team scope: 636 passed. Separate authentication suites: 25 passed with loopback access. The broader earlier run also passed 618 tests; its 23 sandbox-blocked authentication failures were resolved by that rerun.
- The backend run emitted an optional memory warmup warning because `sentence_transformers` is absent; no assertions failed in the final scope.
- Real Chrome composer geometry: 280, 320, 360 and 640px, dark/light/HC/Match VS Code appearances and all three accents, both subscription provider indicators, and Send/chat Stop/task Stop states. Existing transcript contrast and reduced-motion browser tests also passed.
- Actual HTTP route → editor-client → host smoke: initial ownership, selection, live readback, another client's change followed by reconnect, stale-version conflict, and structured restart admission rejection passed against a temporary scripted backend.
- Interactive isolated browser preview: native effort keyboard controls and focus return, Full access warning entry/Tab trapping/Escape cancellation/focus return, and the combined menu were verified. Screenshot: `.tmp/theming-phase3-preview.png`.
- Applied the project-local web-design-guidelines skill, using the [current upstream Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md), to the changed UI. Remaining deliberate choices: compact controls follow the approved desktop composer; image paste is consumed only for the existing attachment flow; host-managed extension state does not use browser URLs; task Stop-and-revert retains its existing behavior.

## Installed-app boundary

The reviewed local package is `.tmp/theme3-handoff/crucible-phase3-reviewed.vsix`, built with the validated runtime manifest and installed successfully. The managed backend is already an editable install pointing at this checkout, so the backend changes are available on its next restart after integration. The source manifest and lockfiles were preserved.

The user's active VS Code session was not reloaded or interrupted. A fresh installed-app model turn and the full live editor-theme matrix remain manual checks after reload; the isolated preview and automated browser matrix are the visual evidence for this phase. Reload VS Code to load the new extension bundle and backend source.

## Composer width follow-up

The composer wrapper now uses the transcript's centered 760px maximum and 16px side padding. Reviewed the complete follow-up diff: the composer remains pinned at the bottom, shrinks with narrow panels, and retains the existing popovers and controls. No blocking findings. The 17 ThreadView tests and six real-browser footer matrix cases passed; the webview build and extension typecheck/build passed. The isolated preview measured equal 276px margins at 1280px, and 16px margins without horizontal overflow at 280px and 320px. The updated local package is `.tmp/theme3-handoff/crucible-composer-width-reviewed.vsix`.
