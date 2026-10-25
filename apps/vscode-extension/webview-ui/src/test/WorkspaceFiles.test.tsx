import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import DesktopApp from "../desktop/DesktopApp";
import { focusRefreshPaths } from "../desktop/useProjectFiles";
import type { DirectoryState } from "../desktop/useProjectFiles";
import type { WorkspacePort } from "../desktop/workspaceClient";
import type { BrowserDirectory } from "../../../../desktop/src/workspace-browser";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const host = (data: unknown) => act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
function ready(id = "a") {
  host({ type: "renderThreadList", threads: ["a", "b"].map(threadId => ({ threadId, workspacePath: "/project", title: `Conversation ${threadId}`, createdAt: "2026-10-21T10:00:00Z" })), activeThreadId: id });
  host({ type: "clearThread", threadId: id }); host({ type: "liveStatus", threadId: id, turnActive: false, agentsRunning: 0 });
}
beforeEach(() => { window.innerWidth = 1440; });
function fixture() {
  const page: BrowserDirectory = { path: "", captured: 5, limited: false, warnings: [], entries: [
    { path: "nested", name: "nested", kind: "directory", hidden: false, ignoreStatus: "included" },
    { path: "sample.ts", name: "sample.ts", kind: "file", hidden: false, ignoreStatus: "included" },
    { path: ".hidden", name: ".hidden", kind: "file", hidden: true, ignoreStatus: "included" },
    { path: ".git", name: ".git", kind: "protected", hidden: true, ignoreStatus: "included" },
    { path: "outside", name: "outside", kind: "symlink", hidden: false, targetLocation: "outside", linkTarget: "/outside", ignoreStatus: "included" },
  ] };
  const port: WorkspacePort = { list: vi.fn<WorkspacePort["list"]>(async path => path ? { ...page, path, captured: 0, entries: [] } : page),
    preview: vi.fn<WorkspacePort["preview"]>(async path => ({ path, size: 14, kind: "text", truncated: false, text: "const value = 1" })),
    search: vi.fn(async () => ({ entries: [page.entries[2]!], scanned: 5, partial: false, warnings: [] })), cancelSearch: vi.fn() };
  return { page, port };
}
function openFilesWorkspace() {
  fireEvent.click(screen.getByRole("button", { name: "Open workspace" }));
  const launcher = screen.queryByRole("region", { name: "New tab" });
  if (launcher) fireEvent.click(within(launcher).getByRole("button", { name: "Files" }));
}
it("keeps real file tabs and selection at project scope while preserving the existing composer", async () => {
  const { port } = fixture(); render(<DesktopApp fileStore={port} />); ready();
  const input = screen.getByRole("textbox", { name: "Chat input" }); fireEvent.change(input, { target: { value: "layout draft" } });
  openFilesWorkspace();
  expect(await screen.findByRole("button", { name: "sample.ts" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: ".git Protected" })).toHaveAttribute("aria-disabled", "true"); expect(screen.getByRole("button", { name: "outside Link" })).toHaveAttribute("aria-disabled", "true");
  fireEvent.click(screen.getByRole("button", { name: "nested" })); expect(await screen.findByText("Empty folder")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "sample.ts" })); expect(await screen.findByLabelText("Read-only source sample.ts")).toHaveTextContent("const value = 1");
  fireEvent.click(screen.getByRole("button", { name: ".hidden" })); expect(await screen.findByLabelText("Read-only source .hidden")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Toggle file navigator" })); expect(screen.queryByRole("complementary", { name: "Workspace files" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Expand workspace" })); fireEvent.click(screen.getByRole("button", { name: "Restore conversation" }));
  expect(input).toHaveValue("layout draft"); fireEvent.click(screen.getByRole("button", { name: "Hide workspace" }));
  openFilesWorkspace(); expect(screen.getByLabelText("Read-only source .hidden")).toBeInTheDocument();
  ready("b"); expect(screen.getByLabelText("Read-only source .hidden")).toBeInTheDocument(); expect(screen.getByRole("button", { name: "Close sample.ts tab" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close .hidden tab" })); expect(screen.getByLabelText("Read-only source sample.ts")).toBeInTheDocument();
});
it("filters paths, exposes ignored choice and rejects late preview resurrection after close", async () => {
  const { port } = fixture(); let resolve!: (value: Awaited<ReturnType<WorkspacePort["preview"]>>) => void;
  vi.mocked(port.preview).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  render(<DesktopApp fileStore={port} />); ready(); openFilesWorkspace();
  fireEvent.click(await screen.findByRole("button", { name: "sample.ts" })); fireEvent.click(screen.getByRole("button", { name: "Close sample.ts tab" }));
  await act(async () => resolve({ path: "sample.ts", kind: "text", size: 1, text: "late", truncated: false })); expect(screen.queryByText("late")).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Filter workspace paths" }), { target: { value: "hidden" } });
  await waitFor(() => expect(port.search).toHaveBeenCalledWith("hidden", false));
  fireEvent.click(screen.getByRole("checkbox", { name: "Include ignored" })); await waitFor(() => expect(port.search).toHaveBeenCalledWith("hidden", true));
  expect(port.cancelSearch).toHaveBeenCalled();
});

it("caps focus refresh to visible folders and never resets paged folders", () => {
  const entries = Array.from({ length: 70 }, (_, index) => ({ path: `dir-${index}`, name: `dir-${index}`, kind: "directory" as const, hidden: false, ignoreStatus: "included" as const }));
  const expanded = new Set(["", ...entries.map(entry => entry.path), "collapsed/nested"]);
  const directories: Record<string, DirectoryState> = { "": { loading: false, page: { path: "", entries, captured: 70, limited: false, warnings: [] } }, "dir-0": { loading: false, paged: true } };
  const paths = focusRefreshPaths(expanded, directories);
  expect(paths).toHaveLength(16); expect(paths).not.toContain("dir-0"); expect(paths).not.toContain("collapsed/nested");
});
it("does not reload the current page or overflow on repeated native focus events", async () => {
  const { page, port } = fixture(); vi.mocked(port.list).mockResolvedValueOnce({ ...page, nextCursor: "next" }).mockResolvedValueOnce({ ...page, entries: [], nextCursor: undefined });
  render(<DesktopApp fileStore={port} />); ready(); openFilesWorkspace();
  fireEvent.click(await screen.findByRole("button", { name: "Next 200 entries" }));
  await screen.findByRole("button", { name: "First page" }); vi.mocked(port.list).mockClear();
  act(() => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("focus")); });
  expect(port.list).not.toHaveBeenCalled(); expect(screen.getByRole("button", { name: "First page" })).toBeInTheDocument();
});
it("reuses Markdown safely, opens relative file tabs and exposes native file actions", async () => {
  const { page, port } = fixture(); page.entries.push({ path: "README.md", name: "README.md", kind: "file", hidden: false, ignoreStatus: "included" });
  vi.mocked(port.preview).mockImplementation(async path => ({ path, size: 100, kind: "text", truncated: false, text: path === "README.md" ? "# Project guide\n\n[Source](sample.ts)\n\n![remote](https://example.com/image.png)\n\n<script>unsafe()</script>" : "current source" }));
  port.action = vi.fn(async () => ({ message: "Native action completed" }));
  render(<DesktopApp fileStore={port} />); ready(); openFilesWorkspace(); fireEvent.click(await screen.findByRole("button", { name: "README.md" }));
  expect(await screen.findByRole("heading", { name: "Project guide" })).toBeInTheDocument(); expect(document.querySelector("script")).toBeNull(); expect(document.querySelector("img")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "View source" })); expect(screen.getByLabelText("Read-only source README.md")).toHaveTextContent("<script>unsafe()</script>");
  fireEvent.click(screen.getByRole("button", { name: "Preview" })); fireEvent.click(screen.getByRole("link", { name: "Source" }));
  expect(await screen.findByLabelText("Read-only source sample.ts")).toHaveTextContent("current source");
  fireEvent.click(screen.getByRole("button", { name: "File actions" })); fireEvent.click(screen.getByRole("button", { name: "Copy path" })); await waitFor(() => expect(port.action).toHaveBeenCalledWith("desktop/filesCopyPath", "sample.ts"));
});
it("marks only applied live edits stale and routes transcript file events without losing the draft", async () => {
  const { port } = fixture(); render(<DesktopApp fileStore={port} />); ready();
  const input = screen.getByRole("textbox", { name: "Chat input" }); fireEvent.change(input, { target: { value: "keep this draft" } });
  host({ type: "desktop/fileOpen", path: "sample.ts" }); expect(await screen.findByLabelText("Read-only source sample.ts")).toBeInTheDocument();
  host({ type: "appendMessage", message: { role: "agent", type: "diff_card", taskId: "edit", content: "", timestamp: "now", metadata: { diff_entries: [{ path: "sample.ts", additions: 1, deletions: 0 }] } } });
  expect(screen.queryByRole("button", { name: "Reload changed preview" })).toBeNull();
  host({ type: "resolveInlineChangeCard", taskId: "edit", resolution: "applied" });
  expect(screen.getByRole("button", { name: "Reload changed preview" })).toBeInTheDocument(); expect(input).toHaveValue("keep this draft");
  fireEvent.click(screen.getByRole("button", { name: "Reload" })); await waitFor(() => expect(screen.queryByRole("button", { name: "Reload changed preview" })).toBeNull());
});
it("shows local images with bounded residency and keeps potential animation opt-in", async () => {
  const { page, port } = fixture();
  for (const path of ["a.png", "b.png", "c.gif"]) page.entries.push({ path, name: path, kind: "file", hidden: false, ignoreStatus: "included" });
  port.image = vi.fn<NonNullable<WorkspacePort["image"]>>(async path => ({ path, kind: "image", size: 1, sha256: path, width: 1, height: 1, animated: path.endsWith(".gif"), mediaType: path.endsWith(".gif") ? "image/gif" : "image/png", dataUrl: "data:image/png;base64,AA==" }));
  render(<DesktopApp fileStore={port} />); ready(); openFilesWorkspace();
  for (const path of ["a.png", "b.png", "c.gif"]) { fireEvent.click((await screen.findAllByRole("button", { name: path }))[0]!); await waitFor(() => expect(port.image).toHaveBeenCalledWith(path)); }
  expect(await screen.findByRole("button", { name: "Show image (may animate)" })).toBeInTheDocument(); expect(screen.queryByRole("img")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show image (may animate)" })); expect(screen.getByRole("img", { name: "c.gif" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Hide animated image" })); expect(screen.queryByRole("img")).toBeNull();
});

it("marks externally changed open previews stale without replacing their body or composer draft", async () => {
  const { port } = fixture();
  vi.mocked(port.preview).mockResolvedValue({ path: "sample.ts", kind: "text", text: "old source", size: 10, truncated: false, revision: "before" });
  port.metadata = vi.fn(async () => ({ entries: [{ path: "sample.ts", revision: "after" }] }));
  render(<DesktopApp fileStore={port} />); ready();
  fireEvent.change(screen.getByRole("textbox", { name: "Chat input" }), { target: { value: "retain draft" } });
  openFilesWorkspace(); fireEvent.click(await screen.findByRole("button", { name: "sample.ts" }));
  await screen.findByText("old source"); act(() => { window.dispatchEvent(new Event("focus")); });
  expect(await screen.findByRole("button", { name: "Reload changed preview" })).toBeInTheDocument();
  expect(screen.getByText("old source")).toBeInTheDocument(); expect(screen.getByRole("textbox", { name: "Chat input" })).toHaveValue("retain draft");
  expect(port.metadata).toHaveBeenCalledWith(["sample.ts"]);
});

it("checks a manually loaded Markdown image and reloads it after an external change", async () => {
  const { page, port } = fixture(); page.entries.push({ path: "README.md", name: "README.md", kind: "file", hidden: false, ignoreStatus: "included" });
  vi.mocked(port.preview).mockResolvedValue({ path: "README.md", kind: "text", text: "![local](image.png)", size: 19, truncated: false, revision: "document" });
  port.image = vi.fn(async (path: string) => ({ path, kind: "image" as const, size: 1, sha256: "image", revision: "old image", width: 1, height: 1, animated: false, mediaType: "image/png" as const, dataUrl: "data:image/png;base64,AA==" }));
  port.metadata = vi.fn<NonNullable<WorkspacePort["metadata"]>>(async paths => ({ entries: paths.map(path => ({ path, revision: path === "README.md" ? "document" : "new image" })) }));
  render(<DesktopApp fileStore={port} />); ready(); openFilesWorkspace(); fireEvent.click(await screen.findByRole("button", { name: "README.md" }));
  fireEvent.click(await screen.findByRole("button", { name: "Show local image: image.png" })); await screen.findByRole("img", { name: "image.png" });
  act(() => { window.dispatchEvent(new Event("focus")); }); expect(await screen.findByText("Changed — reload this image.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reload changed preview" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reload image" })); await waitFor(() => expect(port.image).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText("Changed — reload this image.")).toBeNull());
});

it("shows only real changed-path evidence, preserves folder dots, and clears conversation provenance on switch", async () => {
  const { port } = fixture(); render(<DesktopApp fileStore={port} />); ready(); openFilesWorkspace();
  expect(screen.queryByRole("region", { name: "Changed files" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Changed files" })).toBeNull();
  host({ type: "appendMessage", message: { role: "agent", type: "diff_card", content: "", timestamp: "now", metadata: { resolved: "applied", diff_entries: [{ path: "nested/actual.ts" }] } } });
  const changed = screen.getByRole("region", { name: "Changed files" });
  fireEvent.click(within(changed).getByRole("button", { name: "nested Changed" }));
  expect(within(changed).getByRole("button", { name: "actual.ts Edited Changed" })).toBeInTheDocument();
  expect(within(changed).queryByText("sample.ts")).toBeNull();
  expect(await screen.findByRole("button", { name: "nested Contains changed files" })).toBeInTheDocument();
  const outside = screen.getByRole("button", { name: "outside Link" }); expect(outside.title).toContain("outside the project"); expect(outside.title).not.toContain("/outside");
  expect(screen.getByText("Link outside the project; links are not followed")).toHaveClass("sr-only");
  ready("b"); expect(screen.queryByRole("region", { name: "Changed files" })).toBeNull();
});
