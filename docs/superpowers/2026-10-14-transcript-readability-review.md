# Transcript readability follow-up

14 October 2026. Refines the completed transcript following the user's comparison screenshots and feedback that Crucible felt cluttered and cramped. Reviewed the complete follow-up against `345db9f` before integration. This was a separate review pass by Codex, without an independent reviewer or sub-agent.

## Result

No blocking findings. The centered 760px column and pinned composer remain. The reading scale is 15px at the default workbench font size, with 1.65 line height, 24px between transcript items, and 16px between paragraphs. Headings and nested lists have explicit spacing. Code keeps the 13px body scale. Tables have more padding and horizontal rules rather than a full grid. Collapsed activity summaries use readable muted text, an activity icon and a chevron, with their full-width dividers removed.

Reviewed shared effects: all uses of the prose token follow the workbench font size; streaming answers match finished answers; PlanCard shares the table styling; both Markdown table renderers retain their own overflow wrapper. No message grouping, content, tool results, gate actions, or approval policy changed. Failed-command details still expand and retain their outputs. The design document records this refinement to the earlier density choices.

## Validation

- 113 focused component, Markdown, theme-lint and type-scale tests passed, plus 13 PlanCard tests.
- Eight real-browser cases passed: Markdown wrapping in nested surfaces and transcript geometry/contrast/reduced motion, covering 280/360/640/1024px as applicable, all four appearances and all three accents.
- Webview typecheck and production build passed. The unchanged extension host bundle is reused from the preceding verified composer-width package.
- Interactive isolated preview: expanded activity and failed-command output were checked. At 320px the transcript and long-path table had no horizontal overflow. At 1280px the column measured 760px, item gaps 24px, prose 15px, paragraph margins 16px, code 13px, table padding 8px/12px, and activity border 0px.
- Screenshot: `.tmp/transcript-readability-preview.png`. Local package: `.tmp/theme3-handoff/crucible-transcript-rhythm-reviewed.vsix`.

The screenshot uses representative fixture content in the real transcript components. The active VS Code session is left running; reload is needed to assess this build with the user's full thread and editor theme. Nothing is pushed.

## Team board width follow-up

Following the user's clarification, only the team window's horizontal positioning changes: centered, maximum 760px wide, shrinking to the panel width minus its existing 12px side margins. Its full-height presentation, top/bottom offsets, backdrop, scrolling and member tabs are preserved. Reviewed the complete follow-up against `16ec2c8`; no blocking findings. All 15 team-window tests and the production webview build passed. The real-component browser preview measured 760px width at 1280px (260px equal margins), and 296px width at 320px (12px equal margins), with no horizontal overflow. Both retained the existing 40px top and 12px bottom offsets, and the board body scrolled internally. Screenshot: `.tmp/team-board-width-preview.png`; local package: `.tmp/theme3-handoff/crucible-team-board-width-reviewed.vsix`. The extension host bundle is unchanged.

## Sub-agent transcript follow-up

Expanded sub-agent transcripts mapped each message/tool directly rather than using the main chat's grouping. Extracted the existing `TranscriptItems` renderer for all three surfaces, with a child adapter that separates completed work from the current batch. Inline and floating child views now use the same collapsed summaries, prose scale and spacing, with plain report text. Team member chapters use the same work renderer instead of their extra Explored disclosure; their chapter boundaries, handed messages, edits and reports remain. Stopped children keep unfinished calls inspectable as Tool activity without a false active-work line; approval-waiting members show Waiting for your approval. The main chat retains its existing gates, rewind, plan versions, queued messages and end-of-turn changes card.

Reviewed the complete follow-up against `559c904`, including shared-renderer defaults, React keys, live-to-history grouping, current-step isolation and terminal/report fallback. No blocking findings. The full webview suite passed 1,071 tests with the existing performance opt-in skipped. Final targeted checks cover grouping, failed output access, narration/activation boundaries, live sealing without duplicate calls or lost expansion, stopped calls, current member work, waiting approval, and existing main-chat behavior. Webview typecheck and production build passed. Browser preview verified keyboard summary expansion, failed-command output, collapsed default state and zero horizontal overflow at 320px. Screenshot: `.tmp/subagent-transcript-preview.png`; local package: `.tmp/theme3-handoff/crucible-subagent-transcript-reviewed.vsix`. The host/backend are unchanged; reload VS Code for the installed UI.

## Summary icon follow-up

The shared collapsed activity header now uses the first nonempty action's icon: the existing tool-icon mapping for tool calls, diff for edits, and an outcome icon for outcome-only groups. Unknown tools keep their existing fallback. Reviewed the full change against `d06a1fd`; labels, grouping, disclosure behavior and decorative-icon accessibility are preserved. The 26 activity/sub-agent/member tests, webview typecheck and production build passed. The browser preview verified a file icon on a file-first mixed group and a terminal icon on a command-first group. Screenshot: `.tmp/activity-summary-icon-preview.png`; local package: `.tmp/theme3-handoff/crucible-activity-summary-icon-reviewed.vsix`.

## Handed post disclosure follow-up

Team member handed posts now start with an excerpt of the first nonempty line, limited to 160 Unicode code points. Longer or multiline posts have native Show more / Show less buttons with expanded state and a unique controlled-body reference. Expansion restores the exact full message, including later paragraphs; short plain posts need no extra control. The author and board reference remain visible. Reviewed the complete change against `c9562e7`; no blocking findings. The 26 member/team-window tests, four real-browser wrapping cases, webview typecheck and build passed. The browser preview verified Enter expansion, final-paragraph preservation, Show less, and no horizontal overflow in expanded/collapsed states at 320px. The browser wrapping test still exercises the full disclosed message at 280/360/640px. Screenshot: `.tmp/handed-post-preview.png`; local package: `.tmp/theme3-handoff/crucible-handed-post-disclosure-reviewed.vsix`.

## Selected thread header fix

Switching a thread loaded its messages but never updated the webview's active thread ID, so the header kept looking up the initial thread. Transcript hydration now carries the selected ID in the existing clearThread message, through the controller, production UI adapter and ChatPanel. The reducer selects that ID while clearing the old transcript. Open, switch and new-thread paths supply it; routine clears without an ID preserve selection. Existing navigation guards prevent delayed older loads from overwriting a newer selection. No extra list fetch or thread-list replacement is needed.

Reviewed the complete change against `365abeb`; no blocking findings. The 94 controller/panel and 58 state/header tests passed, covering selected IDs, new-thread creation, routine clears, title updates and delayed switches. Extension and webview typechecks/builds passed. The real App preview verified opening the second thread, returning to the first, and opening the second again, with matching headers and messages. Screenshot: `.tmp/thread-title-preview.png`; local package: `.tmp/theme3-handoff/crucible-thread-header-selection-reviewed.vsix`. Reload VS Code to load the updated host and webview.
