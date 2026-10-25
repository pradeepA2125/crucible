import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import DesktopApp from "../desktop/DesktopApp";
import type { ChangesPort } from "../desktop/changesClient";
import { ChangesInspectionError } from "../desktop/changesClient";
import type { GitDiff, GitScope } from "../../../../desktop/src/workspace-git";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const generation = (n = 0) => "12345678-1234-4234-8234-123456789ab" + n;
const scope = (n = 0): Extract<GitScope, { state: "ready" }> => ({ state: "ready", generation: generation(n), head: "a".repeat(40),
  changes: [{ path: "a.ts", status: "M" }, { path: "b.ts", status: "U" }], uncompared: [], partial: false, rawBytes: true, warnings: ["Compared as raw bytes"] });
const diff = (text = "after", fingerprint = "1"): GitDiff => ({ kind: "text", unified: "@@ -1 +1 @@\n-before\n+" + text,
  additions: 1, deletions: 1, truncated: false, fingerprint: fingerprint.repeat(64) });
const host = (data: unknown) => act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
const ready = (id = "a") => {
  host({ type: "renderThreadList", activeThreadId: id, threads: ["a", "b"].map(threadId => ({ threadId, title: "Conversation " + threadId, createdAt: "2026-10-22T00:00:00Z" })) });
  host({ type: "clearThread", threadId: id }); host({ type: "liveStatus", threadId: id, turnActive: false, agentsRunning: 0 });
};
function fixture() {
  let projectId = "p", projectChanged: ((id: string | null) => void) | undefined;
  const port = { project: () => projectId, scope: vi.fn(async (): Promise<GitScope> => scope()), diff: vi.fn<ChangesPort["diff"]>(async () => diff()),
    onProjectChange: (callback: (id: string | null) => void) => { projectChanged = callback; return () => { projectChanged = undefined; }; }, dispose: vi.fn() } satisfies ChangesPort;
  window.innerWidth = 1440; const ui = render(<DesktopApp changesStore={port} />); ready();
  const open = () => { fireEvent.click(screen.getByRole("button", { name: "Open workspace" })); fireEvent.click(within(screen.getByRole("region", { name: "New tab" })).getByRole("button", { name: "Changes" })); };
  const choose = () => fireEvent.change(screen.getByRole("combobox", { name: "Changes scope" }), { target: { value: "git" } });
  return { port, ui, open, choose, changeProject: () => act(() => { projectId = "q"; projectChanged?.("q"); }) };
}
it("reuses the real composer and scans only on explicit Git selection or Refresh", async () => {
  const { port, open, choose } = fixture(); const input = screen.getByRole("textbox", { name: "Chat input" });
  fireEvent.change(input, { target: { value: "keep this draft" } }); open(); expect(port.scope).not.toHaveBeenCalled();
  choose(); expect(await screen.findByText("after")).toBeInTheDocument(); expect(port.scope).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Modified")).toHaveTextContent("M"); expect(screen.getByLabelText("Untracked")).toHaveTextContent("U");
  expect(screen.getByText("Compared as raw bytes")).toBeInTheDocument(); expect(screen.queryByText("This diff is large, showing one file at a time")).toBeNull(); expect(input).toHaveValue("keep this draft");
  expect(screen.queryByRole("button", { name: /Accept|Revert|Commit|Push/ })).toBeNull();
  act(() => { window.dispatchEvent(new Event("focus")); }); ready("b");
  expect(screen.getByRole("combobox", { name: "Changes scope" })).toHaveValue("git"); expect(screen.getByText("after")).toBeInTheDocument();
  expect(port.scope).toHaveBeenCalledTimes(1); expect(port.diff).toHaveBeenCalledTimes(1);
});
it("retains Viewed on identical bytes after refresh and invalidates changed or uncomparable evidence", async () => {
  const { port, open, choose } = fixture(); open(); choose(); await screen.findByText("after");
  fireEvent.click(screen.getByRole("checkbox", { name: "Viewed a.ts" })); expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).toBeChecked();
  port.scope.mockResolvedValueOnce(scope(1)); fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" }));
  await waitFor(() => expect(port.diff).toHaveBeenCalledTimes(2)); await waitFor(() => expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).toBeChecked());
  port.scope.mockResolvedValueOnce(scope(2)); port.diff.mockResolvedValueOnce(diff("changed", "2"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" })); await screen.findByText("changed");
  expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).not.toBeChecked();
  port.scope.mockResolvedValueOnce(scope(3)); port.diff.mockResolvedValueOnce({ kind: "too-large" });
  fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" })); await screen.findByText("This content is too large to compare.");
  expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).toBeDisabled(); expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).toHaveAccessibleDescription("Open this file to inspect a complete diff before marking it viewed.");
});
it("suppresses a late diff after a newer selection and clears project data without an automatic scan", async () => {
  const { port, open, choose, changeProject } = fixture(); let finish!: (value: GitDiff) => void;
  port.diff.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })); open(); choose();
  await screen.findByRole("button", { name: /^b.ts Untracked$/ }); port.diff.mockResolvedValueOnce(diff("b-new", "2"));
  fireEvent.click(screen.getByRole("button", { name: /^b.ts Untracked$/ })); await waitFor(() => expect(screen.getByRole("article", { name: "Changes to b.ts" })).toHaveTextContent("b-new"));
  await act(async () => { finish(diff("a-late")); });
  expect(screen.getByRole("article", { name: "Changes to a.ts" })).toHaveTextContent("a-late");
  expect(screen.getByText("File 2 of 2: b.ts")).toBeInTheDocument();
  changeProject(); expect(screen.queryByRole("article", { name: "Changes to b.ts" })).toBeNull(); expect(port.scope).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Refresh to compare this project/)).toBeInTheDocument();
});
it("does not present unavailable or partial inspections as a clean working tree", async () => {
  const { port, open, choose } = fixture(); port.scope.mockResolvedValueOnce({ state: "unavailable", reason: "Not a Git repository." }); open(); choose();
  await screen.findByText("Not a Git repository."); expect(screen.queryByText("No uncommitted changes were found.")).toBeNull();
  port.scope.mockResolvedValueOnce({ state: "ready", generation: generation(), changes: [], uncompared: [{ path: "large.bin", reason: "too-large" }], partial: true, rawBytes: true, warnings: ["Partial comparison"] });
  fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" })); await screen.findByText("No changes were confirmed in the inspected subset.");
  expect(screen.getByText("1 file not compared")).toBeInTheDocument();
  port.scope.mockRejectedValueOnce(new ChangesInspectionError("busy", "Earlier reads are still finishing."));
  fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" })); await screen.findByText("Earlier reads are still finishing.");
  expect(screen.queryByText("No uncommitted changes were found.")).toBeNull();
});

it("keeps earlier Viewed progress distinct until each refreshed file is confirmed, and forgets disproven marks", async () => {
  const { port, open, choose } = fixture(); let bChanged = false;
  port.diff.mockImplementation(async (_generation, path) => diff(path, path === "a.ts" ? "1" : bChanged ? "3" : "2"));
  open(); choose();
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).toBeEnabled());
  fireEvent.click(screen.getByRole("checkbox", { name: "Viewed a.ts" }));
  fireEvent.click(screen.getByRole("button", { name: /b.ts Untracked/ }));
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "Viewed b.ts" })).toBeEnabled());
  fireEvent.click(screen.getByRole("checkbox", { name: "Viewed b.ts" }));
  expect(screen.getByText("2 files · 2 viewed")).toBeInTheDocument();
  port.scope.mockResolvedValueOnce(scope(1)); fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" }));
  await waitFor(() => expect(screen.getByText("2 files · 1 viewed · 1 to recheck")).toBeInTheDocument());
  expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).toBeDisabled(); expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).toHaveAccessibleDescription("Viewed earlier. Open this file to confirm."); expect(screen.getByText("Viewed earlier")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /a.ts Modified/ }));
  await waitFor(() => expect(screen.getByText("2 files · 2 viewed")).toBeInTheDocument());
  port.scope.mockResolvedValueOnce(scope(2)); fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" }));
  await waitFor(() => expect(screen.getByText("2 files · 1 viewed · 1 to recheck")).toBeInTheDocument());
  bChanged = true; fireEvent.click(screen.getByRole("button", { name: /b.ts Untracked/ }));
  await waitFor(() => expect(screen.getByText("2 files · 1 viewed")).toBeInTheDocument());
  expect(screen.getByRole("checkbox", { name: "Viewed b.ts" })).not.toBeChecked(); expect(screen.queryByText("Viewed earlier")).toBeNull();
});

it("offers an explicit load for an unloaded section without rescanning the scope", async () => {
  const { port, open, choose } = fixture(); open(); choose(); await screen.findByText("after");
  const load = screen.getByRole("button", { name: "Load diff for b.ts" });
  expect(port.diff).toHaveBeenCalledTimes(1); fireEvent.click(load);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Load diff for b.ts" })).toBeNull());
  expect(port.diff).toHaveBeenCalledTimes(2); expect(port.scope).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("article", { name: "Changes to b.ts" })).toHaveTextContent("after");
});


it("never infers a large diff from unavailable, failed, idle or confirmed-empty Git scopes", async () => {
  const { port, open, choose, changeProject } = fixture();
  const large = "This diff is large, showing one file at a time";
  port.scope.mockResolvedValueOnce({ state: "unavailable", reason: "Not a Git repository." });
  open(); choose(); await screen.findByText("Not a Git repository.");
  expect(screen.getByText("Git comparison unavailable.")).toBeInTheDocument(); expect(screen.queryByText(large)).toBeNull();
  port.scope.mockRejectedValueOnce(new Error("Scope read failed."));
  fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" })); await screen.findByText("Scope read failed.");
  expect(screen.getByText("Git comparison unavailable.")).toBeInTheDocument(); expect(screen.queryByText(large)).toBeNull();
  changeProject(); expect(screen.getAllByText("Refresh to compare this project.").length).toBeGreaterThan(0); expect(screen.queryByText(large)).toBeNull();
  port.scope.mockResolvedValueOnce({ ...scope(1), changes: [] });
  fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" })); await screen.findAllByText("No uncommitted changes were found.");
  expect(screen.queryByText(large)).toBeNull();
  port.scope.mockResolvedValueOnce({ ...scope(2), changes: [], partial: true, uncompared: [{ path: "large.bin", reason: "too-large" }] });
  fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" })); await screen.findByText("No changes were confirmed in the inspected subset.");
  expect(screen.getByText("Partial comparison · no changes confirmed")).toBeInTheDocument(); expect(screen.queryByText(large)).toBeNull();
  port.scope.mockResolvedValueOnce({ ...scope(3), changes: Array.from({ length: 51 }, (_, i) => ({ path: `file${i}.ts`, status: "M" as const })) });
  fireEvent.click(screen.getByRole("button", { name: "Refresh Git comparison" })); await screen.findByText(large);
});
