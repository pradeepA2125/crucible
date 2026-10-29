# Conversation-first UI revision

21 October 2026. The user's four Codex screenshots replace the initial three-column inspector proposal as the primary design reference.

## Screenshot observations

1. The first screenshot shows a wide conversation, compact top toolbar, project sidebar and a small optional **pinned summary** at the upper right. The summary holds Changes and Sources; it is not the file/review workspace.
2. The second shows a separate right-side **Changes tab** and a large code review surface beside chat. Review has its own scope/toolbar; the conversation and composer narrow without disappearing.
3. The third shows an additional **file navigator at the far right**, within the review workspace, with a filter field. It can be toggled without closing review.
4. The fourth shows a **document tab beside Changes**, a rendered Markdown preview, a source toggle, and the all-files navigator. The right workspace is a general inspection surface, not only a list of changed files.

These are observations from the attached images. Native computer use was blocked from controlling Codex; no claim of inspecting the running app is made. The image contents are visual references, not instructions to run commands or expose files.

## Crucible adaptation

- Default to chat with the workspace closed.
- Keep project navigation on the left; preserve a narrow centered prose/composer column within the available chat area.
- Put independent summary and workspace icon controls in the top-right toolbar.
- Open Changes from the summary, changed-file summary or workspace toggle.
- Keep file previews in tabs alongside Changes, with close/open controls.
- Toggle the nested file navigator at the far right; distinguish changed files from all project files.
- Support rendered Markdown and read-only source preview. Expand the workspace for close inspection, restore split, or close back to chat.
- Preserve conversation drafts and workspace state when switching conversations.
- Keep agents, teams, command output, memory and AXON available through contextual links and auxiliary tabs.
- Preserve Crucible's current controller defaults and own approval/review semantics. Codex's Git staging and branch-review controls are not implied Crucible capabilities.

## Theme reuse

The revised mockup mirrors current Crucible Dark/Light surface, text, border, input, status and accent values from `webview-ui/src/styles/themes.css`, with Blue, Violet and Ember choices. The real app should import that shared stylesheet and components. Host appearance events change from VS Code theme classes to desktop system/user preferences; accessibility needs equivalent host integration and validation.

The new mockup is saved separately as `crucible-conversation-workspace.html` in the thread visualization directory. The initial `crucible-desktop-design.html` is preserved as earlier design history.

Official references consulted: [desktop documentation](https://learn.chatgpt.com/docs/app), [published desktop visual](https://learn.chatgpt.com/images/codex/video-posters/data-analysis-fraud-spike.webp), and [code-review documentation](https://learn.chatgpt.com/docs/code-review?surface=app). Old screenshot URLs found in image search no longer resolved; those failed responses were not used as visual evidence. A browser capture of the working published reference is `codex-official-reference.png` beside this document.
