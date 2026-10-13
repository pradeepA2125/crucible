# Phase 2 transcript review

Date: 14 October 2026
Reviewer: Codex in the continuation chat
Scope: the full Phase 2 diff, `5f6d6bf..0419e86`, followed by verification of the corrective changes below. Phase 3 has not started.

This separate review occurred after the first fast-forward onto `feat/codex-style-chat-theming`. The earlier pass was implementation self-review and an accessibility audit; it did not replace this whole-phase review.

## Findings

| Priority | Finding | Trigger and impact | Resolution |
|---|---|---|---|
| P2 | Expanded tool details can widen the transcript | Expanding a tool with a long name, thought or input lets the new implicit grid column grow to its minimum content width. The 280px browser fixture overflowed by 9,330px. Previously the new browser coverage exercised only closed lines. | `ActivityLine` uses a `minmax(0,1fr)` column and a `min-w-0` details wrapper. `ToolDetailPanel` wraps long header and input text. The browser regression opens the real component before measuring it. |
| P2 | User-bubble code and queued labels lose the approved contrast | Mixing 16% white into the code-chip background lowers white-text contrast to 3.63:1 for dark Blue and 3.89:1 for light Blue. Queued-label opacity also reduces contrast on most accent fills. | A semantic `--user-code-bg` darkens the fill instead; high contrast uses a transparent chip. Queued text inherits the full paired foreground without opacity. Browser checks require at least 4.5:1 for bubble, code and queued text across Blue, Violet and Ember. |
| P2 | The browser fixture does not select the actual accent palette | The fixture set `data-theme` and `data-accent`, but omitted `data-tone`, which selects accent values in `themes.css`. It allowed a transparent bubble to pass its weak foreground/background inequality check. | The fixture now sets the tone, iterates all accents and checks an opaque bubble and measured text contrast. |

All three findings are fixed. No further actionable findings remain in the reviewed Phase 2 diff.

## Review coverage

- Inspected the column/tail/avatar changes, all tool-track replacement sites, the pure tool describer, user mentions, changes-card expansion, theme/type/radius changes, decoration removal and the host test-harness adjustment.
- Checked the plan's five review focus cases: long unbroken answers, missing or unusual tool arguments, capped activity detail scrolling, keyboard-operable mentions, and the three-versus-four changed-files boundary.
- Existing gates, file-open messages, diff decisions, team links, streaming grouping and state contracts retain their behavior. Backend and protocol implementation did not change.
- Removed stale component comments left by the radius codemod.

## Verification

- Reproduced the expanded-detail overflow in real Chrome before fixing it.
- After the layout fix, reproduced the inline-code contrast failures with the corrected palette fixture before fixing them.
- Focused regressions: 97 passed.
- Full webview suite: 1,048 passed; one existing opt-in performance test skipped.
- Webview TypeScript check passed.
- Full monorepo build passed during packaging.
- The previously passing editor-client and extension suites were unchanged by these review fixes.

## Live-check limits

The earlier installed-app visual check and computed-colour probe remain the evidence for the Match VS Code transcript. Expanded layout, every accent, own light/dark palettes, high-contrast fallback and reduced motion are covered by the improved real-browser fixture. A fresh live model turn and interactive side-panel/theme checks remain pending. The review did not interrupt the user's active VS Code session.

The reviewed local package is `.tmp/theme2-handoff/crucible-phase2-reviewed.vsix`. It updates the installed extension; reloading webviews picks up the new bundle. Nothing was pushed.
