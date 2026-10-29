# Crucible React UI guidelines

24 October 2026. Pradeep requires these conventions for **every remaining desktop checkpoint**, including review corrections. Functional completion alone does not approve a prototype-looking interface. Use the existing React UI as the visual source of truth; the completed sidebar/terminal refresh provides the desktop example.

## Reuse the actual components

The shared source is `apps/vscode-extension/webview-ui/src/`. Import the existing composer, transcript, approval/command/edit cards, session/agent/team views and Settings. A desktop feature adds host integration and shell composition, not a simplified copy of those components. Use `components/shared/CardShell.tsx`, `buttons.tsx` (`BtnPrimary`, `BtnGhost`, `BtnDanger`), `Icon.tsx`, Settings `SectionHeader.tsx` and `useModalFocus.ts` directly where appropriate. Do not create an alternative button/card/icon library. Native OS dialogs are appropriate for actual OS permissions and last-resort recovery; normal product confirmations use shared React presentation.

## Use one theme and scale

`styles/themes.css`, `index.css` and `theme.ts` own appearance. Read semantic tokens; never add a parallel desktop palette or hardcoded light/dark colors.

| Purpose | Existing tokens |
|---|---|
| Surfaces and text | `--surface-0` through `--surface-3`, `--text-1`, `--text-2`, `--text-3` |
| Borders and focus | `--border`, `--border-strong`, `--focus-ring` |
| Accent | `--accent`, `--accent-bg`, `--accent-bg-2`, `--accent-border`, `--accent-fill`, `--on-accent` |
| State | `--success`, `--warning`, `--danger` and their background/border tokens |
| Radius | `--r-control` (6px), `--r-card` (10px), `--r-composer` (16px), `--r-round` |
| Type | `--fs-caption`, `--fs-meta`, `--fs-body`, `--fs-prose` |
| Depth and motion | `--shadow-control`, `--shadow-popover`, `--highlight-inset`, existing duration/easing tokens |

These values adapt to user font size, Follow system, Dark/Light/high contrast and Blue/Violet/Ember. Use the semantic tokens or their existing Tailwind aliases; do not scatter literal radii/colors to approximate them. Prefer spacing from the existing 4/8/12/16/24px rhythm. Restrained borders and existing inset/popover shadows establish hierarchy; avoid decorative gradients and new glows.

## Compose clean controls

- Related information belongs in one rounded card with a clear title, secondary metadata and compact actions. Integrate trailing menus with the row rather than shrinking the shared row into a second nested card.
- Use clear selected, hover, keyboard-focus, disabled, pending and error states. Selected accent must survive hover. Preserve hit areas and visible focus; color alone must not carry state.
- Give inputs and selectors the existing input surface/border. Flex children need `min-width: 0`; allow actions to wrap before labels become unreadable. Long paths wrap; titles may ellipsize with an accessible full label. Avoid page-level horizontal overflow.
- Empty/loading/unavailable states are intentional layouts with existing icons, short explanations and the relevant real action. Do not fill empty space with implementation details, raw process tokens, IDs or repeated warnings.
- Explain consequential choices where users decide. Keep routine counts/status concise and put optional limits/details in accessible disclosures. Avoid duplicate sidebar status tiles with different destinations; Runtime lives in Settings→Runtime, ownership/trust in the footer.
- App-local preferences belong in a normal named Settings section and Overview card, not above the entire Settings page. Desktop-only features must remain absent from VS Code's settings registry.
- Modal dialogs require a title, focus trap/restoration, Escape/Cancel, bounded scrolling and meaningful action labels. Do not imply that work continues after the process quits unless that execution model actually exists.

## Preserve behavior and review the result

Visual work must preserve original handlers, project/thread identity, approval/trust rules, drafts, lifecycle accounting and current reactive defaults. Tasks remain off. New controls must be wired to actual authoritative state; do not ship mock counts or simulated terminals.

Each checkpoint handoff must link this guide and list reused primitives. The independent reviewer checks both correctness and visual consistency. Verify the actual built app in Dark/Light and wide/narrow layouts, with a representative populated state and relevant keyboard/error/empty states. Use focused behavior tests and the existing theme tests; avoid tests that merely repeat CSS implementation. Record actual screenshots and distinguish source coverage, native observation and deferred platform evidence. The user’s latest design feedback takes precedence over older mockups.
