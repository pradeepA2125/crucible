import { afterEach, expect, it, vi } from "vitest";
import { mkdtemp, realpath, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parsePayload } from "../src/contract.js";
import { refusalMessages } from "../src/refusal.js";
import { createWorkspaceFiles } from "../src/workspace-files.js";
import { createWorkspaceBrowser } from "../src/workspace-browser.js";
import { createWorkspaceFileRequests, type WorkspaceFileRequest } from "../src/workspace-file-requests.js";
const roots: string[] = [];
async function fixture() { const root = await realpath(await mkdtemp(join(tmpdir(), "crucible-browser-"))); roots.push(root); return { root, files: await createWorkspaceFiles(root) }; }
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
it("lists all entries with pattern metadata and filters ignored descendants only when requested", async () => {
  const { root, files } = await fixture(); await mkdir(join(root, "ignored")); await mkdir(join(root, ".hidden"));
  await writeFile(join(root, ".gitignore"), "ignored/\n"); await writeFile(join(root, "ignored", "sample.txt"), "ignored"); await writeFile(join(root, ".hidden", "sample.txt"), "hidden");
  const browser = createWorkspaceBrowser(files), signal = new AbortController().signal;
  expect((await browser.list("")).entries).toEqual(expect.arrayContaining([expect.objectContaining({ path: "ignored", ignoreStatus: "ignored" }), expect.objectContaining({ path: ".hidden", kind: "directory" })]));
  expect((await browser.search("sample", false, signal)).entries.map(entry => entry.path)).toEqual([".hidden/sample.txt"]);
  expect((await browser.search("sample", true, signal)).entries.map(entry => entry.path).sort()).toEqual([".hidden/sample.txt", "ignored/sample.txt"]);
});
it("marks result caps partial and cancels a pending walk before consuming its page", async () => {
  const { root, files } = await fixture();
  await Promise.all(Array.from({ length: 501 }, (_, index) => writeFile(join(root, `sample-${index}`), "")));
  const result = await createWorkspaceBrowser(files).search("sample", true, new AbortController().signal);
  expect(result.entries).toHaveLength(500); expect(result.partial).toBe(true); expect(result.warnings.length).toBeGreaterThan(0);
  let release!: (value: Awaited<ReturnType<typeof files.list>>) => void;
  const list = vi.fn(() => new Promise<Awaited<ReturnType<typeof files.list>>>(resolve => { release = resolve; }));
  const controller = new AbortController(), search = createWorkspaceBrowser({ ...files, list }).search("sample", false, controller.signal);
  controller.abort(); release({ path: "", entries: [], captured: 0, limited: false });
  await expect(search).rejects.toThrow(); expect(list).toHaveBeenCalledTimes(1);
});
it("suppresses stale replies, bounds concurrency, cancels predecessor searches and never exposes internal errors", async () => {
  const pending: (() => void)[] = []; let current = { projectId: "project", viewId: "one" };
  const post = vi.fn(), preview = vi.fn(() => new Promise<never>(resolve => { pending.push(() => resolve(undefined as never)); }));
  const search = vi.fn(async (_query: string, _include: boolean, signal: AbortSignal) => new Promise<never>((_resolve, reject) => { signal.addEventListener("abort", () => reject(new Error("cancelled"))); }));
  const browser = { list: vi.fn(), preview, search, image: vi.fn(), metadata: vi.fn() }, broker = createWorkspaceFileRequests(browser, () => current, post);
  const request = (index: number): WorkspaceFileRequest => ({ type: "desktop/filesPreview", requestId: String(index), path: "sample" });
  const runs = [0, 1, 2, 3].map(index => broker.handle(request(index))); await broker.handle(request(4));
  expect(post).toHaveBeenCalledWith(expect.objectContaining({ requestId: "4", reason: "busy" }));
  post.mockClear(); current = { projectId: "project", viewId: "two" }; pending.splice(0).forEach(resolve => resolve()); await Promise.all(runs); expect(post).not.toHaveBeenCalled();
  const first = broker.handle({ type: "desktop/filesSearch", requestId: "old", query: "old", includeIgnored: false });
  const second = broker.handle({ type: "desktop/filesSearch", requestId: "new", query: "new", includeIgnored: false });
  await first; expect(search.mock.calls[0]![2].aborted).toBe(true); expect(post).toHaveBeenCalledWith(expect.objectContaining({ requestId: "old", cancelled: true }));
  await broker.handle({ type: "desktop/filesCancelSearch", requestId: "cancel" }); await second;
  expect(post).toHaveBeenCalledTimes(3); expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ cancelled: true }));
  preview.mockRejectedValueOnce(new Error("private absolute path")); await broker.handle(request(5));
  expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ reason: "unavailable", error: "Files could not be loaded. Refresh and retry." }));
  broker.dispose(); await broker.handle(request(6)); expect(preview).toHaveBeenCalledTimes(5);
});

it("admits only bounded relative Files commands and keeps refusal separate from chat input", () => {
  for (const path of ["../escape", "/outside", "a\\b", "a//b", "a:bad", "a\0b"]) expect(() => parsePayload({ type: "desktop/filesPreview", requestId: "request", path })).toThrow();
  expect(() => parsePayload({ type: "desktop/filesPreview", requestId: "request", path: "" })).toThrow();
  expect(() => parsePayload({ type: "desktop/filesSearch", requestId: "request", query: "x", includeIgnored: "yes" })).toThrow();
  expect(() => parsePayload({ type: "desktop/filesList", requestId: "request", path: "", bypassProtected: true })).toThrow();
  expect(parsePayload({ type: "desktop/filesList", requestId: "request", path: ".hidden" }).path).toBe(".hidden");
  expect(refusalMessages({ type: "desktop/filesPreview", requestId: "request" }, "Refused")).toEqual([{ type: "desktop/filesResult", requestId: "request", error: "Refused" }]);
});

it("project Files survives conversation/backend changes and settles project changes in the same view", async () => {
  let projectId = "one", threadId = "a", processId = "backend-a", finish!: (value: never) => void;
  const preview = vi.fn(() => new Promise<never>(resolve => { finish = resolve; })), post = vi.fn();
  const broker = createWorkspaceFileRequests({ list: vi.fn(), search: vi.fn(), preview, image: vi.fn(), metadata: vi.fn() }, () => ({ projectId, viewId: "view" }), post);
  const first = broker.handle({ type: "desktop/filesPreview", requestId: "first", path: "sample" });
  threadId = "b"; processId = "backend-b"; finish(undefined as never); await first;
  expect(threadId + processId).toBe("bbackend-b"); expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ requestId: "first", result: undefined }));
  const second = broker.handle({ type: "desktop/filesPreview", requestId: "second", path: "sample" });
  projectId = "two"; finish(undefined as never); await second;
  expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ requestId: "second", cancelled: true }));
});

it("validates metadata batches and routes project-scoped results through the bounded broker", async () => {
  const { root, files } = await fixture(); await writeFile(join(root, "sample"), "body");
  expect(parsePayload({ type: "desktop/filesMetadata", requestId: "metadata", paths: ["sample"] })).toMatchObject({ paths: ["sample"] });
  for (const paths of [[], Array.from({ length: 13 }, () => "sample"), ["../outside"], [""]]) expect(() => parsePayload({ type: "desktop/filesMetadata", requestId: "metadata", paths })).toThrow();
  const post = vi.fn(), broker = createWorkspaceFileRequests(createWorkspaceBrowser(files), () => ({ viewId: "view", projectId: "project" }), post);
  await broker.handle({ type: "desktop/filesMetadata", requestId: "metadata", paths: ["sample", "missing", ".git/config"] });
  expect(post).toHaveBeenCalledWith(expect.objectContaining({ requestId: "metadata", result: { entries: [{ path: "sample", revision: expect.stringMatching(/^[a-f0-9]{64}$/) }, { path: "missing", reason: "missing" }, { path: ".git/config", reason: "protected" }] } })); broker.dispose();
});
