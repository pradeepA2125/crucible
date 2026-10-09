# Message file-reference links

Date: 2026-10-09
Status: Design approved in conversation; written specification awaiting user review.

## Goal

When a message references a workspace file, such as `ProviderSection.test.tsx`, render the reference as a clickable link. Open a unique match directly. When multiple files share that name, show a native VS Code file picker with their relative paths. The user explicitly selected the picker behavior and approved the design below.

## Approach and alternatives

Use automatic linking during Markdown rendering. This works for both historical and new messages without rewriting stored content or depending on the model to produce links.

Alternatives considered:

- Model-generated Markdown links require less renderer work but can be omitted or point to invented paths.
- Rewriting raw message strings can damage Markdown syntax and code blocks.

Choose a Markdown-aware transformation rather than either alternative.

## User-visible behavior

- Recognize complete filenames with extensions and workspace-relative file paths in prose and inline code. Multi-dot names such as `ProviderSection.test.tsx` are supported.
- Link a reference only when at least one eligible workspace file matches it. Unknown references remain ordinary text.
- Keep the original visible spelling, surrounding punctuation, and inline-code styling. Link appearance and focus indication must make the reference discoverable and keyboard accessible.
- Do not transform fenced or indented code blocks, existing Markdown links, images, or URL/email autolinks. Never create nested links.
- Treat quotes, parentheses, and sentence-ending punctuation outside a reference as surrounding text, not part of its filename.
- Use exact, case-preserving matches. Accept a leading `./` on relative paths. A reference containing directory components must match that relative path; do not silently fall back to an unrelated basename.
- For a bare filename, match the full basename, not a substring. One match opens directly; multiple matches display a picker with full workspace-relative paths, including workspace-folder identity when needed.
- Cancelling the picker performs no action. A file removed since rendering produces a helpful notice rather than an unhandled error or a new empty editor.

## Architecture and data flow

### Markdown-aware reference transformation

A focused, independently testable helper recognizes references in eligible Markdown nodes and looks them up in a workspace-file catalog. It leaves all unmatched content and excluded nodes unchanged. The helper must not access the filesystem or alter stored messages.

Integrate the helper into shared message Markdown rendering so main-assistant and background-agent messages using that renderer receive the same behavior. Share the helper with other Markdown message-body surfaces where needed for consistent chat and team-message behavior. Do not expand this feature into arbitrary settings or document previews.

Existing evidence: `apps/vscode-extension/webview-ui/src/components/shared/MarkdownContent.tsx` renders content through React Markdown with GFM support and exports shared components and styling. Preserve its existing table and wrapping behavior, including unrelated uncommitted changes.

### Workspace catalog

The extension supplies eligible workspace files and remains authoritative for file identity and opening. Keep actual file URIs in the extension; the webview needs only display paths and matching information. Use a shared catalog rather than filesystem enumeration per message or per render.

Refresh the catalog when the message UI initializes and when eligible files or workspace folders change. A refreshed catalog re-evaluates displayed references. Recheck matches on click so stale catalog entries cannot cause an incorrect open. Respect workspace search exclusions and omit generated/dependency directories using the existing file-list policy. Do not depend solely on the existing composer's 5,000-file cap: message linking must not silently miss eligible files beyond that cap.

Existing evidence: `apps/vscode-extension/src/extension.ts:248-261` supplies workspace-file listing and a workspace-relative open callback; `apps/vscode-extension/src/chat-panel.ts:391-404` bridges those operations. The current open callback joins a path to the first workspace folder, so new reference resolution needs an explicit workspace-aware boundary rather than blindly using that callback for basename matches.

### Reference activation and resolution

Activating a generated link sends a dedicated file-reference request through the webview bridge, not a navigation to a command URI. Validate the request in the extension. Resolve the reference against eligible files in the current workspace folders:

1. For a relative path, use exact relative-path matches; for a basename, use exact basename matches.
2. If there is one match, open its retained URI.
3. If there are multiple matches, show a sorted native Quick Pick with identifying paths and open the chosen URI.
4. If there are no matches, show a concise file-not-found notice.

Opening failures are caught and reported without breaking message rendering. The catalog and activation resolver should use the same matching rules and eligible-file policy.

## Safety

Treat all webview payloads and message content as untrusted. Reject malformed requests, absolute paths, URI schemes, parent-directory traversal, and paths outside workspace roots. Do not interpolate references into shell commands or enable command-URI navigation. Validate selected files at activation time, including canonical containment for local files so a symlink cannot bypass workspace containment. Preserve existing external-link behavior; this feature does not broaden external navigation permissions.

## Scope and non-goals

This is a UI/extension feature; it requires no provider, backend, model-prompt, or stored-history changes. It links references to current workspace files, not shadow-workspace artifacts. It does not add fuzzy filename guessing, arbitrary absolute-path opening, directory links, or line/column navigation. Automatic recognition of filenames containing whitespace is outside the initial scope; existing explicitly authored links retain their current behavior.

## Verification and acceptance criteria

Automated tests must cover:

- A unique `ProviderSection.test.tsx` reference opens the correct URI.
- Duplicate basenames show the picker with distinct paths; selection opens the selected URI and cancellation opens nothing.
- Exact relative paths take precedence over basename guessing, including a leading `./` and duplicate relative paths across workspace folders.
- Multi-dot names, surrounding punctuation, repeated references, and inline-code references retain their displayed text and styling.
- Unknown references stay unlinked; refreshing after file creation or deletion updates displayed links.
- Existing links, URLs, emails, images, fenced code, and indented code remain untouched.
- Deleted files, no workspace, and file-opening errors are handled without throwing into the message UI.
- Forged payloads, traversal, absolute paths, command schemes, and escaping symlinks cannot open files outside the workspace.
- Catalog sharing avoids repeated enumeration for every rendered message and supports eligible files beyond the composer's existing cap.
- Existing Markdown table and wrapping tests continue to pass.

A manual smoke check should verify a clickable filename in an assistant message, inline-code styling, keyboard activation, the native duplicate picker, and a missing-file notice in a real VS Code workspace.

## Implementation boundary

Preserve all unrelated working-tree edits. Write an implementation plan only after the user reviews this specification. The plan must locate and read the current message surfaces, bridge contracts, catalog lifecycle, and test setup before choosing exact implementation files and verification commands.
