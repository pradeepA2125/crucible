# Desktop UI audit after CP12

Requested by Pradeep on 24 October 2026. Sequence: finish CP11, complete and independently review CP12, then audit and correct the UI designed across CP1–11 (including CP12 integration). This is authorized follow-up work; no additional permission is needed for ordinary reversible UI corrections. CP11 is closed after explicit approval to carry unperformed platform tests to CP13 as mandatory release gates. CP12 must close before this audit begins.

## Authority and scope

Use [React UI guidelines](react-ui-guidelines.md) and the actual shared React components/themes as the standard. Preserve the approved Codex-like conversation/workspace arrangement and the requested Changes layout. Do not replace existing good composer, transcript or cards with new copies. Review existing shared views as well as desktop composition; reuse does not exempt a view from visual assessment.

| Surface | Inspect |
|---|---|
| Projects and threads | Project cards, expandable groups, search, attention/pin/archive rows, menus, ownership footer, collapsed and resized sidebar |
| File Explorer and file views | Tree indentation/selection/icons, filter/ignored controls, empty/loading/error states, breadcrumbs, toolbar, code/Markdown preview, tabs and overflow |
| Changes | Conversation/Git/retained-baseline scopes, counts, selectors, file navigator, diff/source layouts, Viewed, review comments, menus, unavailable states and CP12 restore/Undo integration |
| Agents and teams | Existing shared cards, strip, detail views, status/usage, gates and links, workspace integration |
| Terminal and output | Segmented provenance, sessions, toolbars, real terminal containment, output/error/empty states, lifecycle actions |
| Navigation and supporting views | Workspace launcher, command palette, Find, pinned summary, Memory/AXON, expansion and split dividers |
| Settings and confirmations | Overview cards/nav, desktop preferences, drafts/trust/runtime views, Setup, quit/lifecycle and CP12 restore/recovery dialogs |
| Shared chat regression | Original composer/transcript, command/edit/approval cards and theme inheritance remain intact |

## Method and acceptance

Inventory each rendered surface and its source files, reused primitives, and semantic tokens. Record findings with a concrete screenshot/state, severity, cause, and intended correction; compliant surfaces get an explicit pass with evidence. Check typography, spacing, radii, surfaces, borders, selection/hover/focus, disabled/pending/error states, icon consistency and long-label behavior. Dense trees and diffs should remain readable and useful; rounded card styling should establish hierarchy without wrapping every file or code line in a card.

Check the actual built app in Dark/Light, wide/narrow layouts and representative populated states; assess existing accent/high-contrast inheritance through the theme system and relevant supported visual checks. Cover keyboard navigation/focus and overflow. Distinguish actual app observation from source-only checks and unavailable platform coverage. Include VS Code regression for modified shared components.

Use one coherent correction batch and one integrated independent review under collaboration/review-process-v3.md, with one consolidated affected-path correction if needed. Parent remains sole source writer, reviewer read-only for source; preserve before bytes and freeze exact final hashes. Run tests proportionate to behavior changed, affected types/builds and the final native visual matrix. Do not add tests that merely repeat CSS. Preserve handlers, identity/trust/approvals, drafts, navigation, write coordination and lifecycle semantics. Reactive defaults stay on; task mode stays off.

Deliver a findings/resolution ledger, representative before/after screenshots and independent review verdict. Functional success alone does not close visual findings. No commits, pushes, signing or release are implied.

## Completion —25October2026

Audit CLOSED after independent source and final native visual approval. See collaboration/ui-audit-findings.md, collaboration/ui-audit-native.md, SAME collaboration/ui-audit-r0-review-codex-agent.md and previews/ui-audit/index.html. Final source371hashes/19changedpaths,0drift. Fresh VSCode native attempt explicitly uncredited; bounded carried/current evidence and CP13 clean-machine/platform QA disposition recorded in the independent report.
