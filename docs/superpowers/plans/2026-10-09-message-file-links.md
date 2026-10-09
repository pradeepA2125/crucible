# Message File-Reference Links Implementation Plan

## Implementation notes (2026-10-09)

The feature is implemented across chat answers, team post/report Markdown, and
collapsible plan cards. The original plan below records the initial scope; later
user testing expanded it to support `file.py:67`, `file.py:67-87`, and en-dash
line ranges. These resolve the workspace file and reveal the cited line in VS Code.
Authored Markdown links to exact catalogued workspace paths use the same activation
bridge; external links retain their existing behavior.

The inventory prunes generated directories during search, performs bounded parallel
file checks, and refreshes for creates/deletes and configuration/workspace changes.
Content edits do not invalidate the inventory. Clicks reuse a current catalog instead
of rescanning the workspace, while validating target existence and canonical workspace
containment before opening. This addresses a delay exceeding 30 seconds in a workspace
with roughly 80,000 eligible files.

Team member chapters also constrain nested layout widths and wrap handed messages,
long paths, and live status text. Browser regressions cover complete member layouts,
not only isolated Markdown bodies. File-reference tests cover line citations,
activation, duplicate selection, lifecycle, and safe workspace containment.

---

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn exact workspace-file references in message prose and inline code into accessible links that open unique files or offer a native duplicate picker.

**Architecture:** A pure matching module is shared by the extension and webview. One extension-owned catalog retains file URIs, publishes only relative-path metadata, and refreshes on workspace changes; activation resolves again and verifies containment before opening. A message-only Markdown AST transform uses a shared React catalog context, leaving stored messages and existing authored links unchanged.

**Tech Stack:** TypeScript (strict), VS Code workspace/window APIs, Node filesystem APIs, React 18, react-markdown 9, remark-gfm 4, Vitest 3, Testing Library. Add `minimatch` as an explicit extension dependency for scoped search-exclusion matching; do not rely on a transitive dependency.

## Global Constraints

Source specification: `docs/superpowers/specs/2026-10-09-message-file-links-design.md`, approved by the user's “looks good” response.

- “Use exact, case-preserving matches. Accept a leading `./` on relative paths.”
- “A reference containing directory components must match that relative path; do not silently fall back to an unrelated basename.”
- “Keep the original visible spelling, surrounding punctuation, and inline-code styling.”
- “Do not transform fenced or indented code blocks, existing Markdown links, images, or URL/email autolinks. Never create nested links.”
- “Keep actual file URIs in the extension; the webview needs only display paths and matching information.”
- “Do not depend solely on the existing composer's 5,000-file cap: message linking must not silently miss eligible files beyond that cap.”
- “Reject malformed requests, absolute paths, URI schemes, parent-directory traversal, and paths outside workspace roots.”
- “Validate selected files at activation time, including canonical containment for local files so a symlink cannot bypass workspace containment.”
- “Preserve existing external-link behavior; this feature does not broaden external navigation permissions.”
- “It does not add fuzzy filename guessing, arbitrary absolute-path opening, directory links, or line/column navigation.”
- “Automatic recognition of filenames containing whitespace is outside the initial scope; existing explicitly authored links retain their current behavior.”
- No provider, backend, prompt, or stored-history modifications. No command URIs or shell interpolation.
- Preserve all unrelated working-tree changes. In particular, `extension.ts` contains an unrelated composer-model window fix, and `MarkdownContent.tsx` / `PostBody.tsx` contain wrapping changes. Also preserve the untracked composer-model, real-browser wrapping, and README-refresh plan files.
- Keep VS Code's existing `^1.85.0` engine floor. Use `findFiles`, not a newer search API.

---

## Grounded integration map

Read these regions again at execution time; line numbers are current planning anchors, not replacement coordinates:

- `apps/vscode-extension/src/extension.ts:248-261`: composer listing uses `findFiles("**/*", generated-directory glob, 5000)` and opens by joining the first folder. Leave these composer callbacks alone; references need a separate boundary.
- `apps/vscode-extension/src/chat-panel.ts:124-177,225-257,391-404`: positional constructor, webview lifecycle, and composer bridge. Prefer a setter for the new reference service instead of lengthening the positional constructor.
- `apps/vscode-extension/src/vscode-shim.d.ts:45-59,207-218`: this repository declares its own VS Code types. The shim lacks URI scheme/path/authority, filesystem stat, watchers, scoped configuration, and workspace-change events; add the exact APIs used by the adapter.
- `apps/vscode-extension/webview-ui/src/main.tsx:6-10`: mount the catalog provider once, around `App`, inside StrictMode.
- `apps/vscode-extension/webview-ui/src/components/shared/MarkdownContent.tsx:14-27,48-79`: existing shared table components and GFM plugins. Preserve all styling.
- `apps/vscode-extension/webview-ui/src/components/teams/PostBody.tsx:1-19`: another message body using the shared table/GFM styling. Swap only its inner Markdown renderer.
- `apps/vscode-extension/webview-ui/src/components/messages/PlanCard.tsx:115-150`: chat plan Markdown is also a message body. Reuse the new renderer here without changing the card or its actions.
- `apps/vscode-extension/webview-ui/src/types.ts:417,435-502` and `src/vscodeApi.ts`: independently declared bridge unions and the singleton message poster.
- `apps/vscode-extension/webview-ui/src/test/MarkdownContent.test.tsx`: existing table regression tests.
- `apps/vscode-extension/webview-ui/src/test/markdownWrapping.test.tsx`: existing untracked Chrome-based wrapping regression; retain it.
- Host tests run in Node via `apps/vscode-extension/vitest.config.ts`; webview tests run in jsdom with `src/test/setup.ts`. Host tests should use injected ports rather than import a nonexistent runtime `vscode` package.

### File ownership

All paths below are relative to `apps/vscode-extension/` unless explicitly rooted.

| File | Responsibility |
| --- | --- |
| `src/file-references/matching.ts` (new) | Safe reference normalization, DTO, exact basename/path indexes; no Node or VS Code imports. |
| `src/file-references/catalog.ts` (new) | Single-flight refresh, invalidation, snapshots and subscribers; injected inventory port. |
| `src/file-references/workspace-adapter.ts` (new) | VS Code inventory, exclusions, watcher subscriptions, URI identity, stat and canonical containment. |
| `src/file-references/open-reference.ts` (new) | Untrusted activation validation, fresh resolution, picker, selection recheck and notices; injected ports. |
| `src/file-references/bridge.ts` (new) | Runtime-validated dedicated message handling and catalog publication; injected service. |
| `src/chat-panel.ts`, `src/extension.ts`, `src/vscode-shim.d.ts` | Wire the host service and lifecycle without changing composer behavior. |
| `webview-ui/src/file-references/FileReferenceContext.tsx` (new) | One catalog subscription/request per app mount; memoized index. |
| `webview-ui/src/file-references/remarkFileReferences.ts` (new) | Eligible AST transformation, punctuation and inline-code preservation. |
| `webview-ui/src/file-references/MessageMarkdown.tsx` (new) | Marked-link rendering, keyboard/click bridge, ordinary-link preservation. |
| `webview-ui/src/main.tsx`, `webview-ui/src/types.ts`, the three Markdown surfaces above | Attach provider, contract, and renderer. |
| `test/file-reference-matching.test.ts`, `test/file-reference-catalog.test.ts`, `test/file-reference-workspace.test.ts`, `test/file-reference-open.test.ts`, `test/file-reference-bridge.test.ts` (new) | Host/pure acceptance tests. |
| `webview-ui/src/test/fileReferences.test.tsx` (new) | AST/rendering/refresh/accessibility integration tests. |
| `package.json`, root `../../package-lock.json` | Direct `minimatch` dependency and npm workspace lock update. |

No new generic framework or extra panels. The host pure module lives under the existing host `rootDir: src`; the webview imports it at `../../../src/file-references/matching` from its `src/file-references/` directory. This avoids importing webview code into the extension's TypeScript root. No filesystem or URI objects cross that import boundary.

### Shared contracts (define in Task 1)

```ts
export interface FileReferenceEntry {
  workspaceId: string; // opaque stable identifier, NOT a root URI or absolute path
  workspaceLabel: string;
  relativePath: string; // slash-separated, exact case, relative to this folder
}
export interface FileReferenceIndex {
  byPath: ReadonlyMap<string, readonly FileReferenceEntry[]>;
  byBasename: ReadonlyMap<string, readonly FileReferenceEntry[]>;
}
export interface Reference {
  path: string; // normalized: remove one leading ./, never resolve ..
  hasDirectory: boolean;
}
export function parseReference(value: unknown): Reference | null;
export function buildReferenceIndex(entries: readonly FileReferenceEntry[]): FileReferenceIndex;
export function matchReference(index: FileReferenceIndex, value: unknown): readonly FileReferenceEntry[];
```

Bridge messages (define on both sides in Task 4):

```ts
// webview -> host
{ type: "requestFileReferenceCatalog" }
{ type: "openFileReference", reference: string }
// host -> webview
{ type: "fileReferenceCatalog", revision: number, entries: FileReferenceEntry[] }
```

No URI or chosen file ID in activation payload. The host chooses current files using the supplied reference, never a webview-selected URI.

## Execution preparation

- [ ] Record `git status --short` and relevant diffs before editing. If isolating execution, load using-git-worktrees and preserve/carry the relevant uncommitted wrapping changes explicitly; do not execute against an older renderer that omits them.
- [ ] Read the environment profile once. Use the package scripts below, which are grounded in the current manifests. Root npm workspaces install host dependencies; the nested webview has its own install/lock.
- [ ] Run baseline host/webview tests and both typechecks before changes. Record pre-existing failures separately from new failures; never claim the baseline is green without output.

```bash
npm run -w crucible-vscode-extension test
npm run -w crucible-vscode-extension typecheck
npm --prefix apps/vscode-extension/webview-ui test
npm --prefix apps/vscode-extension/webview-ui run typecheck
```

Expected: tests pass and typechecks exit 0. Chrome-dependent wrapping may skip if no browser exists; record the skip, not a fabricated pass.

## Task 1: Pure exact matching and safe reference grammar

**Files:** Create `src/file-references/matching.ts`; create `test/file-reference-matching.test.ts`.

**Interfaces:** Produces the exact shared contracts above. Both host activation and Markdown eligibility consume `matchReference`; do not duplicate normalization.

- [ ] **Step 1: Write failing matching tests.** Include this executable starting suite:

```ts
import { describe, expect, it } from "vitest";
import { buildReferenceIndex, matchReference, parseReference } from "../src/file-references/matching.js";
const entry = (workspaceId: string, relativePath: string) =>
  ({ workspaceId, workspaceLabel: workspaceId, relativePath });
const entries = [entry("one", "src/ProviderSection.test.tsx"),
  entry("one", "test/ProviderSection.test.tsx"),
  entry("two", "src/ProviderSection.test.tsx")];
const index = buildReferenceIndex(entries);
describe("exact reference matching", () => {
  it("matches whole basenames and exact relative paths", () => {
    expect(matchReference(index, "ProviderSection.test.tsx")).toHaveLength(3);
    expect(matchReference(index, "./src/ProviderSection.test.tsx")).toHaveLength(2);
    expect(matchReference(index, "other/ProviderSection.test.tsx")).toEqual([]);
    expect(matchReference(index, "Section.test.tsx")).toEqual([]);
    expect(matchReference(index, "providersection.test.tsx")).toEqual([]);
  });
  it.each([null, {}, [], 42, "", "../a.ts", "src/../a.ts", "/a.ts", "C:/a.ts",
    "C:\\a.ts", "\\\\host\\a.ts", "command:a.ts", "file:a.ts", "https://a.ts",
    "a.ts:12", "a.ts#12", "a.ts?x", "a%2fts", "a b.ts", "a\u0000.ts"])(
    "rejects unsafe input %j", (value) => expect(parseReference(value)).toBeNull());
});
```

- [ ] **Step 2: Run red.** `npm run -w crucible-vscode-extension test -- test/file-reference-matching.test.ts`; expect module-not-found until the module exists.
- [ ] **Step 3: Implement the contracts and grammar.** Retain punctuation handling in the Markdown layer, not in host normalization. Use indexed lookup so many messages do not each scan the catalog:

```ts
export function parseReference(value: unknown): Reference | null {
  if (typeof value !== "string" || value.length === 0 || value.length > 4096) return null;
  if (/[\s\u0000-\u001f\u007f\\:%?#]/u.test(value)) return null;
  const path = value.startsWith("./") ? value.slice(2) : value;
  const parts = path.split("/");
  if (parts.some(p => !p || p === "." || p === "..")) return null;
  const name = parts[parts.length - 1];
  if (!name || !/^.+\.[^./]+$/u.test(name)) return null;
  return { path, hasDirectory: parts.length > 1 };
}
export function buildReferenceIndex(entries: readonly FileReferenceEntry[]): FileReferenceIndex {
  const byPath = new Map<string, FileReferenceEntry[]>();
  const byBasename = new Map<string, FileReferenceEntry[]>();
  for (const entry of entries) {
    if (!parseReference(entry.relativePath)) continue;
    const parts = entry.relativePath.split("/");
    const basename = parts[parts.length - 1]!;
    for (const [map, key] of [[byPath, entry.relativePath], [byBasename, basename]] as const) {
      const bucket = map.get(key) ?? [];
      bucket.push(entry);
      map.set(key, bucket);
    }
  }
  return { byPath, byBasename };
}
export function matchReference(index: FileReferenceIndex, value: unknown): readonly FileReferenceEntry[] {
  const ref = parseReference(value);
  return ref ? (ref.hasDirectory ? index.byPath : index.byBasename).get(ref.path) ?? [] : [];
}
```

The shared module uses indexed access to remain compatible with the webview's ES2020 target; do not raise that target. `./name.ts` intentionally behaves like a basename after normalization. De-duplicate inventory by URI before constructing this index.

- [ ] **Step 4: Run green and typecheck.** Same targeted test plus host typecheck; expect pass. Test 5,001 synthetic entries and an entry at the end to prove no matcher truncation.
- [ ] **Step 5: Commit only this task's files.** `git add apps/vscode-extension/src/file-references/matching.ts apps/vscode-extension/test/file-reference-matching.test.ts && git commit -m "feat: add exact workspace file-reference matching"`.

## Task 2: Shared catalog, scoped inventory, exclusions, and refresh lifecycle

**Files:** Create `src/file-references/catalog.ts`, `src/file-references/workspace-adapter.ts`, `test/file-reference-catalog.test.ts`, `test/file-reference-workspace.test.ts`; modify `src/vscode-shim.d.ts`, extension `package.json`, root npm lock.

**Interfaces:** Consumes Task 1 DTO. Produces:

```ts
export interface RetainedFile<U> { entry: FileReferenceEntry; uri: U; root: U; }
export interface CatalogSnapshot<U> { revision: number; files: readonly RetainedFile<U>[]; }
export interface CatalogPort<U> {
  enumerate(): Promise<RetainedFile<U>[]>;
  onInvalidate(listener: () => void): { dispose(): void };
  reportFailure(error: unknown): void;
}
export class FileReferenceCatalog<U> {
  constructor(port: CatalogPort<U>);
  refresh(): Promise<CatalogSnapshot<U>>; // force a new inventory unless one is in flight
  invalidate(): void; // debounce file/folder/config bursts and publish latest snapshot
  subscribe(listener: (snapshot: CatalogSnapshot<U>) => void): { dispose(): void };
  dispose(): void;
}
export type WorkspaceApi = Pick<typeof import("vscode"), "workspace" | "RelativePattern" | "Uri" | "FileType">;
export function createWorkspaceAdapter(api: WorkspaceApi): {
  port: CatalogPort<import("vscode").Uri>;
  isCurrentFile(file: RetainedFile<import("vscode").Uri>): Promise<boolean>;
  dispose(): void;
};
```

`refresh` always enumerates for activation, but shares concurrent calls. Ordinary message renders never call it. An invalidation occurring during enumeration must schedule another pass and must not publish a stale snapshot as current. Increment revision only on an accepted publication. On search failure, publish an empty catalog and report/log the failure rather than continuing to advertise stale files.

- [ ] **Step 1: Write red catalog tests using an injected port, fake timers, and deferred promises.** Starting test:

```ts
it("shares concurrent refreshes and keeps all eligible entries", async () => {
  const files = Array.from({ length: 5001 }, (_, i) => ({
    entry: { workspaceId: "w", workspaceLabel: "w", relativePath: `src/f${i}.ts` },
    uri: `uri-${i}`, root: "root",
  }));
  const enumerate = vi.fn(async () => files);
  const catalog = new FileReferenceCatalog({ enumerate,
    onInvalidate: () => ({ dispose() {} }), reportFailure: vi.fn() });
  const [a, b] = await Promise.all([catalog.refresh(), catalog.refresh()]);
  expect(enumerate).toHaveBeenCalledTimes(1);
  expect(a).toBe(b);
  expect(a.files).toHaveLength(5001);
  catalog.dispose();
});
```

Also assert: creation/deletion invalidates once per burst, changed folder set invalidates, config changes invalidate, dirty-during-flight reruns, failure clears stale entries, disposal removes subscriptions/timers and prevents later publication.

- [ ] **Step 2: Run red.** `npm run -w crucible-vscode-extension test -- test/file-reference-catalog.test.ts test/file-reference-workspace.test.ts`; expect missing modules.
- [ ] **Step 3: Implement catalog state with one in-flight promise, dirty generation, timer, listener set, and disposed flag.** Core acceptance rule:

```ts
const generation = this.generation;
const files = await this.port.enumerate();
if (this.disposed) return this.snapshot;
if (generation !== this.generation) continue; // inside the refresh loop
this.snapshot = { revision: this.snapshot.revision + 1, files };
for (const listener of this.listeners) listener(this.snapshot);
return this.snapshot;
```

Use `try/finally` to clear the in-flight promise; own the `onInvalidate` disposable in the catalog. Debounce at 100 ms. A refresh requested while the timer is pending cancels the timer and consumes its invalidation. Catch async debounce errors and route them to `reportFailure`, never an unhandled promise.

- [ ] **Step 4: Install the direct matcher dependency.** From repo root: `npm install -w crucible-vscode-extension minimatch@^9`. Review extension manifest/root lock diff; do not touch nested webview dependencies.
- [ ] **Step 5: Add the adapter and its needed shim declarations.** Add to `Uri`: `readonly scheme: string`, `readonly authority: string`, `readonly path: string`. Add `RelativePattern` with `constructor(base: WorkspaceFolder | Uri | string, pattern: string)`. Widen `findFiles` include/exclude to `string | RelativePattern`; add `getWorkspaceFolder(uri)`, scoped `getConfiguration(section?, scope?: Uri)`, `workspace.fs.stat(uri): Thenable<{ type: FileType }>`, `FileType` values Unknown=0, File=1, Directory=2, SymbolicLink=64; add watcher interface with create/change/delete event registration and disposal, `createFileSystemWatcher`, `onDidChangeWorkspaceFolders`, and `onDidChangeConfiguration` with `affectsConfiguration(section: string): boolean`. Match actual API signatures rather than use `any` casts.

Enumerate each current workspace folder with `findFiles(new RelativePattern(folder, "**/*"), undefined)` (no maximum). `undefined` retains VS Code's default files.exclude behavior. Then apply the existing generated/dependency policy and resource-scoped search.exclude policy. Use the current literal policy as a shared constant within this new adapter:

```ts
export const GENERATED_DIRECTORIES = new Set([
  "node_modules", ".git", "dist", "target", "__pycache__", ".venv", ".crucible",
]);
function generated(relativePath: string): boolean {
  return relativePath.split("/").some(part => GENERATED_DIRECTORIES.has(part));
}
```

Calculate `relativePath` from the file's URI path relative to the folder URI path (slash-normalized), not `asRelativePath(..., true)`; that API's folder prefix is presentation, not the matching path. Check scheme/authority and segment containment before retaining. Maintain `workspaceId` as an opaque per-activation folder map ID, never `folder.uri.toString()` in the DTO. Use `workspaceLabel = folder.name`; for duplicate folder names append the folder's index so picker rows are distinct.

Read `getConfiguration("search", folder.uri).get<Record<string, boolean | { when: string }>>("exclude", {})`. Read resource-scoped `files.exclude` too and apply both maps with the same policy during inventory and activation. For each pattern test the full relative path and each parent-directory prefix using `minimatch(candidate, pattern, { dot: true })`; this makes a rule like `**/generated` exclude its descendants. A `true` rule excludes, a `false` rule does not. For `{ when: string }`, substitute `$(basename)` with the basename without its final extension and check the sibling URI with `workspace.fs.stat`. Exclude only when the matching sibling exists; propagate unexpected inventory errors to the catalog, but a missing sibling is not an error. This handles scoped settings and sibling exclusions without a global-path guess. Filter directories via `workspace.fs.stat`; retain only File entries (symlink files may be retained but must pass canonical activation checks in Task 3). Deduplicate by `uri.toString()` and sort deterministically.

Watch `**/*` create/delete/change, workspace-folder changes, and configuration changes affecting `search.exclude` or `files.exclude`. The adapter fans these into a listener set; catalog owns its subscription and adapter owns the VS Code disposables. Do not enumerate per watcher callback directly.

- [ ] **Step 6: Add adapter tests with a mocked API port or a supplied fake VS Code object, not a production runtime import.** Verify per-folder RelativePattern, no maxResults argument, no workspace -> empty, multi-root exact paths, duplicate folder labels, generated dirs, enabled/disabled exclusion rules, sibling `when`, non-file results, URI deduplication, scope/authority containment, and watcher/config/folder disposal. Use the required injected `WorkspaceApi` parameter on `createWorkspaceAdapter`; this module must import VS Code types only, so Node tests can import it without the runtime package. Task 4 calls `createWorkspaceAdapter(vscode)` explicitly.
- [ ] **Step 7: Run green and typecheck.** Same targeted tests and `npm run -w crucible-vscode-extension typecheck`; expect pass.
- [ ] **Step 8: Commit the task files with explicit paths**, including only dependency/lock changes attributable to this task. Commit message: `feat: add shared live workspace file-reference catalog`.

## Task 3: Safe fresh resolution, duplicate picker, and file opening

**Files:** Create `src/file-references/open-reference.ts`, `test/file-reference-open.test.ts`; extend `workspace-adapter.ts` / `test/file-reference-workspace.test.ts` for activation checks.

**Interfaces:** Consumes `RetainedFile<U>`, catalog `refresh`, and Task 1 matcher. Produces:

```ts
export interface OpenReferenceDeps<U> {
  refresh(): Promise<CatalogSnapshot<U>>;
  identity(uri: U): string;
  isCurrentFile(file: RetainedFile<U>): Promise<boolean>;
  pick(files: readonly RetainedFile<U>[]): Promise<RetainedFile<U> | undefined>;
  open(uri: U): Promise<void>;
  notice(message: string): void;
}
export function openFileReference<U>(value: unknown, deps: OpenReferenceDeps<U>): Promise<void>;
```

- [ ] **Step 1: Write red resolver tests.** Starting unique test:

```ts
it("opens the retained URI for a unique full basename", async () => {
  const file = { entry: { workspaceId: "w", workspaceLabel: "w",
    relativePath: "src/ProviderSection.test.tsx" }, uri: "file:///ws/src/ProviderSection.test.tsx", root: "file:///ws" };
  const deps = { refresh: vi.fn(async () => ({ revision: 1, files: [file] })),
    identity: (uri: string) => uri, isCurrentFile: vi.fn(async () => true),
    pick: vi.fn(async () => undefined), open: vi.fn(async () => {}), notice: vi.fn() };
  await openFileReference("ProviderSection.test.tsx", deps);
  expect(deps.open).toHaveBeenCalledWith(file.uri);
  expect(deps.pick).not.toHaveBeenCalled();
});
```

Create tests for duplicate selection/cancel, identical relative paths across roots, `./src/...`, nonexistent directory path (no fallback), removed file after render, removed/changed file while picker is open, no workspace, opener rejection, unsafe payloads (same array as Task 1), and a forged picker result not belonging to current candidates.

- [ ] **Step 2: Run red.** `npm run -w crucible-vscode-extension test -- test/file-reference-open.test.ts`.
- [ ] **Step 3: Implement fresh activation.** Validate first; an invalid reference produces `Cannot open an invalid file reference.` and no inventory/open call. Refresh inventory and map matcher results back to retained files using `(workspaceId, relativePath)`. Sort by workspaceLabel, relativePath, then workspaceId using a stable ordinal comparator. Unique opens directly; duplicates invoke a native Quick Pick through the injected port; cancellation returns silently. After selection refresh again and require the same retained URI identity still matches the original reference. Do not silently substitute a different same-name file. Call `isCurrentFile` immediately before `open`. No matches or failed stat produce `Workspace file not found: ${reference}`. Catch inventory/open failures and notify `Unable to open workspace file: ${reference}`; do not throw into ChatPanel's unrelated chat-error/input-enabled catch.

Core selection recheck:

```ts
const selectedId = deps.identity(selected.uri);
const latest = await deps.refresh();
const latestIndex = buildReferenceIndex(latest.files.map(f => f.entry));
const matches = matchReference(latestIndex, value);
const current = latest.files.find(f => deps.identity(f.uri) === selectedId
  && matches.some(e => e.workspaceId === f.entry.workspaceId && e.relativePath === f.entry.relativePath));
if (!current || !(await deps.isCurrentFile(current))) {
  deps.notice(`Workspace file not found: ${value}`);
  return;
}
await deps.open(current.uri);
```

- [ ] **Step 4: Implement `isCurrentFile`.** Recheck current workspace membership, identical scheme/authority, lexical path containment, current exclusions, and file stat. For local `file` URIs additionally `realpath` both the root and file and require canonical segment containment:

```ts
import { realpath, stat } from "node:fs/promises";
import * as path from "node:path";
export function contained(root: string, target: string): boolean {
  const relative = path.relative(root, target);
  return relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative);
}
const [canonicalRoot, canonicalFile] = await Promise.all([
  realpath(file.root.fsPath), realpath(file.uri.fsPath),
]);
if (!contained(canonicalRoot, canonicalFile)) return false;
return (await stat(canonicalFile)).isFile();
```

Catch missing/inaccessible files as false. Recheck scoped exclusion policy on activation too. For non-local workspace schemes use URI segment containment plus `workspace.fs.stat`; do not pass remote URIs to Node realpath. Do not use URI string prefix checks (`/ws2` is not inside `/ws`). No claim of eliminating filesystem races with an editor open API; these checks protect the requested boundary at activation time.

- [ ] **Step 5: Add real filesystem tests.** Use `mkdtemp`, `writeFile`, `symlink`, and `rm` in finally: outside-target symlink must be false; inside-target symlink is allowed; deleted files false; sibling-prefix path false; directory false. If symlink creation is unavailable, explicitly skip that platform case and report it.
- [ ] **Step 6: Run green.** Resolver + adapter tests and host typecheck; expect pass.
- [ ] **Step 7: Commit only this task's files**, message `feat: safely open and disambiguate message file references`.

## Task 4: Dedicated host bridge and extension-owned service

**Files:** Create `src/file-references/bridge.ts`, `test/file-reference-bridge.test.ts`; modify `src/chat-panel.ts`, `src/extension.ts`, `webview-ui/src/types.ts`.

**Interfaces:** Consumes Tasks 1–3. Produces:

```ts
export interface FileReferenceBridge {
  handle(message: unknown): Promise<boolean>; // true only for a recognized reference request
  dispose(): void;
}
export function createFileReferenceBridge<U>(
  catalog: FileReferenceCatalog<U>,
  open: (reference: unknown) => Promise<void>,
  post: (message: { type: "fileReferenceCatalog"; revision: number;
    entries: FileReferenceEntry[] }) => void,
): FileReferenceBridge;
```

`ChatPanel.setFileReferenceBridge(bridge)` stores it; `ChatPanel.postFileReferenceCatalog(message)` posts to the current panel if present. The extension owns/catalog/adapter/bridge disposables through `context.subscriptions`. Closing a webview does not dispose the extension catalog. A later mount requests a new snapshot.

- [ ] **Step 1: Write red bridge tests.** Drive null, array, primitives, unknown types, both valid messages, malformed reference fields, publication, and disposal through the actual `handle`. Example:

```ts
expect(await bridge.handle(null)).toBe(false);
expect(await bridge.handle({ type: "openFile" })).toBe(false);
expect(await bridge.handle({ type: "requestFileReferenceCatalog" })).toBe(true);
expect(post).toHaveBeenCalledWith({ type: "fileReferenceCatalog", revision: 1,
  entries: [{ workspaceId: "w", workspaceLabel: "w", relativePath: "src/a.ts" }] });
await bridge.handle({ type: "openFileReference", reference: "a.ts" });
expect(open).toHaveBeenCalledWith("a.ts");
```

Assert retained `uri`/`root` never appear in output, and changes republish without new per-message requests. Malformed opens must be recognized but rejected safely through the Task 3 validator, not sent into the old composer open callback.

- [ ] **Step 2: Run red.** `npm run -w crucible-vscode-extension test -- test/file-reference-bridge.test.ts`.
- [ ] **Step 3: Implement the bridge and attach it without constructor changes.** Guard the top of `onDidReceiveMessage`:

```ts
if (!msg || typeof msg !== "object" || Array.isArray(msg)) return;
const m = msg as Record<string, unknown>;
if (m["type"] === "requestFileReferenceCatalog" || m["type"] === "openFileReference") {
  void this.fileReferenceBridge?.handle(msg).catch(() => {
    void vscode.window.showInformationMessage("Unable to refresh workspace file links.");
  });
  return;
}
```

This branch comes before the settings/composer routes. Extend the shim for `showInformationMessage` if the existing declaration does not cover the call. Leave `listWorkspaceFiles`, `workspaceFileList`, and `openFile` unchanged.

In `extension.ts`, immediately after ChatPanel construction create one adapter and one catalog; construct the opener ports with `identity: uri => uri.toString()`, `isCurrentFile: adapter.isCurrentFile`, `open: async uri => { await vscode.window.showTextDocument(uri); }`, and `notice: text => { void vscode.window.showInformationMessage(text); }`. Picker implementation:

```ts
pick: async files => {
  const items = files.map(file => ({
    label: file.entry.relativePath,
    description: file.entry.workspaceLabel,
    detail: `${file.entry.workspaceLabel}/${file.entry.relativePath}`,
    file,
  }));
  return (await vscode.window.showQuickPick(items, {
    placeHolder: "Choose the workspace file to open",
  }))?.file;
},
```

Register adapter/catalog/bridge with context subscriptions and attach bridge via the setter. Bridge request handling calls catalog.refresh; subscribe publishes accepted snapshots. Avoid double posting a refresh through both subscription and request completion. If a request arrives with a cached snapshot and no refresh publication, explicitly send the cached snapshot once.

- [ ] **Step 4: Add the two inbound union cases and outbound DTO case to webview `types.ts`.** Import `FileReferenceEntry` with `import type` from `../../src/file-references/matching` (the types file is one directory shallower than the context). No editor-client/backend contract change.
- [ ] **Step 5: Run green and regression.** Bridge/resolver/catalog targeted tests, full host suite, host typecheck, webview typecheck. Expect no composer/rewind/agent behavior changes.
- [ ] **Step 6: Commit bridge/wiring changes with patch staging on already-dirty files.** Use `git add -p` for `extension.ts`; do not stage the existing composer model-window hunk. Commit message `feat: bridge live message file-reference catalog and activation`.

## Task 5: Markdown-aware linking, shared React catalog, and message integration

**Files:** Create `webview-ui/src/file-references/FileReferenceContext.tsx`, `remarkFileReferences.ts`, `MessageMarkdown.tsx`, `webview-ui/src/test/fileReferences.test.tsx`; modify `webview-ui/src/main.tsx`, `components/shared/MarkdownContent.tsx`, `components/teams/PostBody.tsx`, `components/messages/PlanCard.tsx`.

**Interfaces:** Consumes Task 1 index/parse/match and Task 4 DTO messages. Produces:

```ts
export function FileReferenceProvider({ children }: { children: React.ReactNode }): JSX.Element;
export function useFileReferenceIndex(): FileReferenceIndex; // empty outside provider (SSR/tests)
export function remarkFileReferences(options: { index: FileReferenceIndex }): (tree: unknown) => void;
export function MessageMarkdown({ content }: { content: string }): JSX.Element;
```

Use structurally typed AST nodes local to the plugin (type, value, children, data) if avoiding extra dependencies; never `any`. Transform at remark stage after GFM, not by replacing raw message strings. Do not use a custom URL scheme or override react-markdown's URL sanitizer.

- [ ] **Step 1: Write red rendering/refresh tests.** Mock `../vscodeApi`, mount provider + both message surfaces, and dispatch a catalog event:

```tsx
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const entries = [{ workspaceId: "w", workspaceLabel: "w",
  relativePath: "src/ProviderSection.test.tsx" }];
function publish(entries: FileReferenceEntry[], revision = 1) {
  act(() => window.dispatchEvent(new MessageEvent("message", {
    data: { type: "fileReferenceCatalog", revision, entries },
  })));
}
it("links prose and inline code while preserving displayed spelling", () => {
  render(<FileReferenceProvider><MarkdownContent
    content={'See (ProviderSection.test.tsx), then `./src/ProviderSection.test.tsx`.'}
  /></FileReferenceProvider>);
  publish(entries);
  const links = screen.getAllByRole("link");
  expect(links.map(link => link.textContent)).toEqual([
    "ProviderSection.test.tsx", "./src/ProviderSection.test.tsx",
  ]);
  expect(links[1].querySelector("code")).not.toBeNull();
  fireEvent.click(links[0]);
  expect(vscode.postMessage).toHaveBeenCalledWith({
    type: "openFileReference", reference: "ProviderSection.test.tsx",
  });
});
```

Also render repeated references, exact relative path, punctuation/quotes, unknown names, case mismatch, creation then deletion via revision updates. Assert one catalog request for many message bodies (provider in a non-StrictMode test), no request from a renderer, and effect cleanup during remount. With StrictMode a mount may legitimately request twice; it must not leak listeners or enumerate per message.

Exclusion fixture (these stay unchanged even when `a.ts` is in the catalog):

```ts
const excluded = [
  "[a.ts](https://example.com)", "![a.ts](https://example.com/image)",
  "https://example.com/a.ts", "user@a.ts", "<https://a.ts>",
  "```ts\na.ts\n```", "    a.ts",
].join("\n\n");
```

Assert no generated links for the fixture and no nested `a a`; ordinary link hrefs remain intact. Test inline code with multiple tokens (`a.ts b.ts`), code containing URLs, unknown mixed text, list/table cell references, and non-ASCII multi-dot filenames. Test unsafe longer tokens such as `../a.ts`, `/a.ts`, `command:a.ts`, `a.ts:12`, `https://x/a.ts` do not partially link their safe-looking suffix.

- [ ] **Step 2: Run red.** `npm --prefix apps/vscode-extension/webview-ui test -- src/test/fileReferences.test.tsx`.
- [ ] **Step 3: Implement provider state and lifecycle.** Keep a revision and immutable DTO array in context, build indexes with useMemo once per snapshot, subscribe before posting request, remove listener on cleanup. Validate incoming array/string fields and integer revision; ignore malformed or older revisions. Default context is an empty index for static markup/tests; renders without a provider do not post messages. Mount:

```tsx
<React.StrictMode>
  <FileReferenceProvider><App /></FileReferenceProvider>
</React.StrictMode>
```

- [ ] **Step 4: Implement the AST transform.** Recursively walk children, skipping `link`, `linkReference`, `image`, `imageReference`, `code`, `html`, and definitions entirely. Process only `text` and `inlineCode`; do not re-walk generated nodes. Tokenize maximal non-whitespace chunks first. Strip only outer quotes/brackets/parentheses and terminal sentence punctuation for candidate lookup; leave those characters as separate unchanged nodes. Try the exact chunk before trimming punctuation, so an eligible filename containing punctuation can still match. Validate the entire remaining candidate through Task 1 before lookup. Never search a safe basename substring within an unsafe URL/email/path token. Preserve the original spelling as the link's visible text and activation value, including leading `./`.

Generate ordinary mdast `link` nodes with URL `#workspace-file-reference`, and attach a private marker via `data.hProperties["data-file-reference"]`. React-markdown allows the fragment and carries the marker to the HAST node; the fragment is not an authorization token. For inline-code references, generated link children contain an `inlineCode` node with the original token value. Unmatched spans inside inline code remain inlineCode nodes; preserve literal spacing and complete rendered text. A block `code` node is never split.

Concrete generated node shape:

```ts
const linked = {
  type: "link",
  url: "#workspace-file-reference",
  data: { hProperties: { "data-file-reference": visible } },
  children: [{ type: inline ? "inlineCode" : "text", value: visible }],
};
```

A hand-authored link to that fragment has no marker and follows the existing authored-link path. Raw HTML is not enabled. Add tests for punctuation stripping and whitespace retention before tightening grammar; do not silently broaden activation normalization.

- [ ] **Step 5: Implement marked anchor handling without replacing ordinary links.** `MessageMarkdown` combines existing GFM/table components with the plugin. Import `MARKDOWN_COMPONENTS` and `MARKDOWN_PLUGINS` from a new focused `components/shared/markdownConfig.tsx` if necessary to avoid a circular import (`MarkdownContent` -> `MessageMarkdown` -> `MarkdownContent`). Move only the table component and GFM plugin constant to that new config file and re-export them from `MarkdownContent.tsx` so existing imports keep working. Add that file to this task's ownership.

Anchor override logic:

```tsx
const reference = node?.properties["data-file-reference"];
if (typeof reference !== "string") return <a {...props}>{children}</a>;
return <a {...props} href="#workspace-file-reference"
  className="text-code underline decoration-dotted cursor-pointer focus-visible:outline focus-visible:outline-2"
  title={`Open workspace file: ${reference}`}
  onClick={event => {
    event.preventDefault();
    vscode.postMessage({ type: "openFileReference", reference });
  }}>{children}</a>;
```

Destructure `node` out of ReactMarkdown component props before spreading into the DOM. Use react-markdown `Components` types so HAST node properties are typed. Native anchor Enter activation triggers click; do not use a mouse-only span. Test with `userEvent.tab()` and `userEvent.keyboard("{Enter}")`; assert preventDefault avoids navigation. Do not add target/command permissions or change external-link props.

- [ ] **Step 6: Replace only inner renderers in all three surfaces.** Keep their frames/styles/table classes; substitute:

```tsx
<MessageMarkdown content={content} />
// PostBody uses its existing prop:
<MessageMarkdown content={text} />
```

Remove unused ReactMarkdown imports from those surfaces, preserving compatibility re-exports and wrapping classes. Existing QAMessage/AgentRow/AgentTranscript already consume MarkdownContent, so do not change their message storage or layouts.

- [ ] **Step 7: Run green and Markdown regressions.**

```bash
npm --prefix apps/vscode-extension/webview-ui test -- src/test/fileReferences.test.tsx src/test/MarkdownContent.test.tsx src/test/PlanCard.test.tsx src/test/markdownWrapping.test.tsx
npm --prefix apps/vscode-extension/webview-ui run typecheck
npm --prefix apps/vscode-extension/webview-ui run build
```

Expected: passing targeted tests, exit 0 typecheck/build. Record Chrome skips. Add a regression that rerenders the same message on catalog updates without modifying its content string.

- [ ] **Step 8: Commit task files with patch staging for dirty Markdown surfaces.** Leave previous wrapping hunks unstaged if not already committed by the owner. Message: `feat: link workspace file references in message Markdown`.

## Task 6: Full acceptance, lifecycle regressions, and real VS Code smoke

**Files:** Extend the new tests only as needed; no speculative new features. Update this plan's acceptance results during execution.

**Interfaces:** The completed public contracts above; no new API.

- [ ] **Step 1: Add an end-to-end contract test.** Feed the host bridge's real published DTO into the React provider, click a generated link, forward the recorded `openFileReference` payload into the actual bridge, and assert the resolver's injected opener receives the retained URI. Keep the host test environment Node and webview environment jsdom; share a JSON DTO fixture rather than import VS Code runtime into jsdom. Validate the exact two bridge discriminants and field names in both tests.
- [ ] **Step 2: Add the final regression cases that cross task boundaries.** Verify catalog sharing with many mounted bodies, a 5,001st eligible file, multi-root identical paths, duplicate picker cancel, directory path no fallback, creation/deletion rerender, changed exclusions while picker open, outside symlink refusal, failures reported as notices, and no URIs in webview traffic. Exercise watcher disposal and panel close/reattach initialization, not just happy-path matching.
- [ ] **Step 3: Run full automated verification.** From repo root:

```bash
npm run -w crucible-vscode-extension test
npm run -w crucible-vscode-extension typecheck
npm --prefix apps/vscode-extension/webview-ui test
npm --prefix apps/vscode-extension/webview-ui run typecheck
npm --prefix apps/vscode-extension/webview-ui run build
npm run -w crucible-vscode-extension build
```

Expected: all relevant tests pass, no new type errors, both bundles build. Host build has an existing prebuild that installs/builds the nested webview; inspect any lockfile changes and keep only intended changes. If baseline failures persist, report them with their original evidence and new-test results rather than widening scope.

- [ ] **Step 4: Manual smoke in an Extension Development Host.** Open a real two-folder workspace. Use an assistant/agent message with `ProviderSection.test.tsx`, `./src/ProviderSection.test.tsx`, inline-code references, an external authored link, and a fenced code block. Verify unique open, distinct sorted duplicate picker rows, picker cancellation, Tab/Enter activation with visible focus, preserved code styling, and unchanged external/fenced behavior. Exercise a team board post/report and a plan message. Create/delete a file and observe links appear/disappear. To test the missing-file notice deterministically, leave the native picker open, delete its chosen file from another window, then select it; it must not create an empty editor. Change search.exclude and remove a workspace folder; references must refresh. A symlink to an outside file must be refused. Reload/restore the chat panel and confirm a catalog is requested again. Record unavailable manual steps explicitly; do not claim smoke passed from jsdom.
- [ ] **Step 5: Inspect final diff and request review.** `git diff --check` and compare against the initial dirty-tree snapshot. Check no backend/history changes, no command URIs, no accidental composer-model/wrapping edits removed, and no unrelated files staged. Load requesting-code-review before asking a reviewer to evaluate this feature.
- [ ] **Step 6: Commit only final test corrections** (`test: cover message file-reference lifecycle and safety`). Re-run automated verification after any review correction and before claiming completion.

## Acceptance coverage matrix

| Specification requirement | Implementation | Evidence to collect |
| --- | --- | --- |
| Unique multi-dot basename opens correct retained URI | Tasks 1, 3 | matcher + resolver tests |
| Duplicate picker, distinct roots/paths, cancellation | Tasks 2–4 | inventory + resolver tests + VS Code smoke |
| Exact relative path, ./, no unrelated basename fallback | Tasks 1, 3, 5 | matching + resolver + rendering tests |
| Punctuation, repetition, inline-code style, keyboard access | Task 5 | AST/render tests + Tab/Enter + smoke |
| Unknown text remains plain; creation/deletion updates | Tasks 2, 5 | invalidation + provider revision tests |
| Existing links/images/URLs/emails and block code untouched | Task 5 | excluded fixture + no nested anchors |
| Deleted file, no workspace, errors become notices | Tasks 2–4 | resolver/bridge failure tests |
| Untrusted payloads, absolute/traversal/schemes/escaping symlink | Tasks 1, 3, 4 | grammar + real filesystem + malformed bridge tests |
| Shared catalog, no 5,000 cap, scoped exclusions | Tasks 2, 4, 5 | 5,001 entries + single-flight + adapter arguments/tests |
| Multi-root identity, selected URI still valid on click | Tasks 2–4 | current-candidate recheck + picker deletion smoke |
| Table and wrapping compatibility | Task 5, 6 | existing GFM, PlanCard and real-browser regression tests |
| History unchanged; message surfaces consistent | Tasks 4–6 | DTO-only flow, content rerender test + surface smoke |
| Watcher/context/extension disposal and restore | Tasks 2, 4–6 | disposal tests + reload smoke |

## Execution handoff

This turn writes the plan only, not the feature implementation. After plan review, choose:

1. **Subagent-Driven (recommended):** execute sequential tasks with fresh implementers and review gates. Tasks 2 and 3 share adapter files, so do not dispatch them simultaneously. Task 4 depends on the completed service contracts, and Task 5 depends on bridge DTOs.
2. **Inline Execution:** load executing-plans and implement task-by-task with checkpoints.

For either option, load test-driven-development before feature edits and verification-before-completion before success claims. Use an execution todo list with a separate verification item. Commit only task-owned hunks; never sweep the unrelated dirty tree into a commit.
