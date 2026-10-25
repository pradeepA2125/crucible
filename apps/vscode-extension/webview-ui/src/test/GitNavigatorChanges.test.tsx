import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import DesktopApp from "../desktop/DesktopApp";
import type { ChangesPort } from "../desktop/changesClient";
import { WorkspaceNavigator } from "../desktop/WorkspaceFiles";
import { GitNavigatorChanges } from "../desktop/GitNavigatorChanges";
import { useProjectFiles } from "../desktop/useProjectFiles";
import type { GitReviewModel } from "../desktop/useGitChanges";
import type { WorkspacePort } from "../desktop/workspaceClient";
import type { GitScope } from "../../../../desktop/src/workspace-git";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const scope = (): Extract<GitScope, { state: "ready" }> => ({ state: "ready", generation: "12345678-1234-4234-8234-123456789abc", head: "a".repeat(40), rawBytes: true, partial: false, warnings: ["Compared as raw bytes"], uncompared: [], changes: [{ path: "sample.ts", status: "M" }, { path: "gone.ts", status: "D" }, { path: "renamed.ts", from: "old.ts", status: "R" }] });
function git(result?: GitScope): GitReviewModel {
  return { enabled: true, result, loading: false, error: "", selected: undefined, detail: undefined, known: new Map(), capturedAt: result ? Date.now() - 120000 : undefined,
    refresh: vi.fn(async () => {}), select: vi.fn(), needsRecheck: () => false, pendingViewed: 0, canView: () => false, isViewed: () => false, setViewed: vi.fn(), openFile: undefined };
}
function fixture() {
  const port: WorkspacePort = { list: vi.fn(async () => ({ path: "", entries: [{ path: "sample.ts", name: "sample.ts", kind: "file" as const, hidden: false, ignoreStatus: "included" as const }], warnings: [], captured: 1, limited: false })), preview: vi.fn(async path => ({ path, kind: "text" as const, size: 1, text: "x", truncated: false })), search: vi.fn(async () => ({ entries: [], scanned: 0, partial: false, warnings: [] })), cancelSearch: vi.fn() };
  const review = vi.fn();
  function Harness({ model }: { model: GitReviewModel }) { const files = useProjectFiles(port); return <WorkspaceNavigator model={files} git={model} reviewGit={review} />; }
  return { port, review, Harness };
}
it("uses cached Git status on real tree rows and routes deleted/renamed changes without preview reads or scans", async () => {
  const { port, review, Harness } = fixture(), model = git(scope()); render(<Harness model={model} />);
  const all = screen.getByRole("region", { name: "All files" });
  expect(await within(all).findByRole("button", { name: "sample.ts Modified" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Changed files scope" })).toHaveValue("conversation");
  expect(screen.getByText("No observed conversation edits or changed previews.")).toBeInTheDocument();
  expect(screen.getByText(/Cached Git comparison.*checked 2 min ago.*HEAD aaaaaaaa/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "Changed files scope" }), { target: { value: "git" } });
  const changes = screen.getByRole("region", { name: "Changed files" });
  fireEvent.click(within(changes).getByRole("button", { name: "gone.ts Deleted" })); expect(review).toHaveBeenLastCalledWith("gone.ts");
  const renamed = within(changes).getByRole("button", { name: "renamed.ts Renamed" }); expect(renamed).toHaveAttribute("title", "old.ts → renamed.ts");
  fireEvent.click(renamed); expect(review).toHaveBeenLastCalledWith("renamed.ts"); expect(port.preview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace files" })); await waitFor(() => expect(port.list).toHaveBeenCalledTimes(2));
  fireEvent.change(screen.getByRole("textbox", { name: "Filter workspace paths" }), { target: { value: "sample" } });
  await waitFor(() => expect(port.search).toHaveBeenCalledWith("sample", false)); expect(model.refresh).not.toHaveBeenCalled(); expect(model.select).not.toHaveBeenCalled();
});
it("labels missing, partial and cleared project evidence honestly without inventing a clean state", async () => {
  const { review, Harness } = fixture(); const ui = render(<Harness model={git()} />);
  fireEvent.change(screen.getByRole("combobox", { name: "Changed files scope" }), { target: { value: "git" } });
  expect(screen.getByText("Git status has not been checked.")).toBeInTheDocument(); expect(screen.queryByText(/No uncommitted changes were found/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open Git changes" })); expect(review).toHaveBeenCalledWith();
  ui.rerender(<Harness model={git({ ...scope(), state: "ready", partial: true, changes: [], uncompared: [{ path: "large.bin", reason: "too-large" }] })} />);
  expect(screen.getByText("Partial raw-byte comparison · 1 not compared")).toBeInTheDocument(); expect(screen.getByText("No changes were confirmed in the inspected subset.")).toBeInTheDocument();
  ui.rerender(<Harness model={git()} />); expect(screen.queryByText(/Cached Git comparison/)).toBeNull(); expect(screen.getByText("No cached Git changes to show.")).toBeInTheDocument(); await waitFor(() => expect(screen.getByRole("button", { name: "sample.ts" })).toBeInTheDocument());
});
it("bounds cached changed rows and reports the omitted paths", () => {
  const value = scope(); if (value.state !== "ready") throw Error("fixture"); value.changes = Array.from({ length: 501 }, (_, n) => ({ path: `nested/file-${n}.ts`, status: "U" as const }));
  render(<GitNavigatorChanges model={git(value)} review={vi.fn()} />);
  const first = screen.getByRole("button", { name: "nested/file-0.ts Untracked" }); expect(within(first).getByText("file-0.ts")).toBeInTheDocument(); expect(within(first).getByText("nested")).toBeInTheDocument(); expect(screen.getAllByRole("button")).toHaveLength(500); expect(screen.getByText("Showing the first 500 changed paths.")).toBeInTheDocument(); expect(screen.queryByText("file-500.ts")).toBeNull();
});

it("opens exact cached Git rows in the desktop Changes tab without rescanning or losing the shared draft", async () => {
  window.innerWidth = 1440; const { port } = fixture();
  const data = scope(); if (data.state !== "ready") throw Error("fixture");
  const changes: ChangesPort = { project: () => "p", onProjectChange: () => () => {}, dispose: vi.fn(), scope: vi.fn(async () => data), diff: vi.fn<ChangesPort["diff"]>(async (_generation, path) => ({ kind: "text", unified: "@@ -1 +1 @@\n-old\n+" + path, additions: 1, deletions: 1, truncated: false, fingerprint: "a".repeat(64) })) };
  render(<DesktopApp fileStore={port} changesStore={changes} />);
  act(() => { for (const message of [{ type: "renderThreadList", activeThreadId: "t", threads: [{ threadId: "t", title: "Test", createdAt: "2026-10-22T00:00:00Z" }] }, { type: "clearThread", threadId: "t" }, { type: "liveStatus", threadId: "t", turnActive: false, agentsRunning: 0 }]) window.dispatchEvent(new MessageEvent("message", { data: message })); });
  const input = screen.getByRole("textbox", { name: "Chat input" }); fireEvent.change(input, { target: { value: "keep explorer review draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Open workspace" })); fireEvent.click(within(screen.getByRole("region", { name: "New tab" })).getByRole("button", { name: "Changes" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Changes scope" }), { target: { value: "git" } });
  await screen.findByRole("article", { name: "Changes to sample.ts" });
  fireEvent.click(screen.getByRole("button", { name: "New workspace tab" })); fireEvent.click(within(screen.getByRole("region", { name: "New tab" })).getByRole("button", { name: "Files" }));
  await screen.findByRole("button", { name: "sample.ts Modified" });
  fireEvent.change(screen.getByRole("combobox", { name: "Changed files scope" }), { target: { value: "git" } });
  fireEvent.click(within(screen.getByRole("region", { name: "Changed files" })).getByRole("button", { name: "gone.ts Deleted" }));
  await screen.findByRole("article", { name: "Changes to gone.ts" });
  await waitFor(() => expect(changes.diff).toHaveBeenLastCalledWith(data.generation, "gone.ts"));
  expect(changes.scope).toHaveBeenCalledTimes(1); expect(port.preview).not.toHaveBeenCalled(); expect(input).toHaveValue("keep explorer review draft");
  expect(screen.getByRole("combobox", { name: "Changes scope" })).toHaveValue("git");
});
