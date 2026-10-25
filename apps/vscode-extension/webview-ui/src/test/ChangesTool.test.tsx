import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ChangesTool, type ChangesModel } from "../desktop/ChangesTool";
import { conversationChanges, DIFF_TRUNCATION_MARKER } from "../desktop/conversationChanges";
import { ChangesRoute } from "../components/activity/changesRoute";
import { TurnChanges } from "../components/activity/TurnChanges";
import DesktopApp from "../desktop/DesktopApp";
import type { ChatMsg, DiffEntry } from "../types";
import { vscode } from "../vscodeApi";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const patch = "--- a/a.ts\n+++ b/a.ts\n@@ -1 +1 @@\n-before\n+after";
const record = (id = "edit", diff: string | null = patch, resolved = "applied"): ChatMsg => ({
  id, role: "agent", type: "diff_card", content: "", timestamp: "2026-10-22T00:00:00Z",
  metadata: { resolved, diff_entries: [{ path: "a.ts", additions: 1, deletions: 1, ...(diff === null ? {} : { unified_diff: diff }) }] },
});
const model = (messages = [record()]): ChangesModel => ({ changes: conversationChanges(messages), select: vi.fn(), isViewed: () => false, setViewed: vi.fn() });
const host = (data: unknown) => act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
const ready = (id = "a") => { host({ type: "renderThreadList", activeThreadId: id, threads: ["a", "b"].map(threadId => ({ threadId, title: `Conversation ${threadId}`, createdAt: "2026-10-22T00:00:00Z" })) }); host({ type: "clearThread", threadId: id }); host({ type: "liveStatus", threadId: id, turnActive: false, agentsRunning: 0 }); };
it("uses applied records only and distinguishes unavailable or shortened history", () => {
  const pending = record("pending"); pending.metadata.resolved = undefined;
  const result = conversationChanges([pending, record("discarded", patch, "discarded"), record("old", null), record("new", patch + "\n" + DIFF_TRUNCATION_MARKER)]);
  expect(result.files).toHaveLength(1); expect(result.files[0].edits).toHaveLength(2);
  expect(result.files[0].edits[0].diff).toBeUndefined(); expect(result.files[0].edits[1]).toMatchObject({ diff: patch, truncated: true });
  render(<ChangesTool model={model([record("old", null), record("new", patch + "\n" + DIFF_TRUNCATION_MARKER)])} />);
  expect(screen.getByText("No diff text was recorded for this edit.")).toBeInTheDocument();
  expect(screen.getByText(/Only a bounded portion/)).toBeInTheDocument(); expect(screen.queryByRole("button", { name: /Accept|Revert/ })).toBeNull();
});
it("invalidates Viewed signatures when the recorded bytes change under the same message id", () => {
  expect(conversationChanges([record()]).files[0].signature).not.toBe(conversationChanges([record("edit", patch + " changed")]).files[0].signature);
});
it("bounds old uncapped records and pages repeated edits without substituting current content", () => {
  const long = conversationChanges([record("long", "@@ -1 +1 @@\n" + "+line\n".repeat(5000))]);
  expect(long.files[0].edits[0].diff!.split("\n").length).toBeLessThanOrEqual(400); expect(long.files[0].edits[0].truncated).toBe(true);
  render(<ChangesTool model={model(Array.from({ length: 8 }, (_, i) => record(`edit-${i}`, "@@ -1 +1 @@\n" + "+line\n".repeat(500))))} />);
  expect(screen.getAllByRole("region", { name: /Edit \d+ of/ })).toHaveLength(5);
  fireEvent.click(screen.getByRole("button", { name: "Next edits" }));
  expect(screen.getByText("Edits 6–8 of 8")).toBeInTheDocument(); expect(screen.getAllByRole("region", { name: /Edit \d+ of/ })).toHaveLength(3);
});
it("routes only desktop applied links while VS Code retains native open and inline diffs", () => {
  const route = vi.fn(), file = { path: "a.ts", additions: 1, deletions: 1, applied: true, diff: patch };
  const ui = render(<ChangesRoute.Provider value={route}><TurnChanges files={[file]} /></ChangesRoute.Provider>);
  fireEvent.click(screen.getByRole("button", { name: "a.ts" })); expect(route).toHaveBeenCalledWith("a.ts");
  ui.unmount(); render(<TurnChanges files={[file]} />);
  fireEvent.click(screen.getByRole("button", { name: "a.ts" })); expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFile", path: "a.ts" });
  fireEvent.click(screen.getByRole("button", { name: "Show diff for a.ts" })); expect(screen.getByText("after")).toBeInTheDocument();
});
it("preserves the shared draft, isolates conversations and resets Viewed on new evidence", () => {
  window.innerWidth = 1440; render(<DesktopApp />); ready(); host({ type: "appendMessage", message: record() });
  const input = screen.getByRole("textbox", { name: "Chat input" }); fireEvent.change(input, { target: { value: "existing shared draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Show diff for a.ts in Changes" }));
  const review = screen.getByRole("region", { name: "Changes" }); expect(within(review).getByText("after")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Toggle file navigator" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Viewed a.ts" })); expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).toBeChecked();
  host({ type: "appendMessage", message: record("second") }); expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).not.toBeChecked();
  expect(input).toHaveValue("existing shared draft"); ready("b"); expect(screen.getByText("No edits have been applied in this conversation.")).toBeInTheDocument();
  expect(screen.queryByText("after")).toBeNull();
});

it("keeps Viewed through live-to-persisted reconciliation and invalidates changed evidence", () => {
  window.innerWidth = 1440; render(<DesktopApp />); ready();
  const live = record(); delete live.id;
  host({ type: "appendMessage", message: live });
  fireEvent.click(screen.getByRole("button", { name: "Show diff for a.ts in Changes" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Viewed a.ts" }));
  host({ type: "replaceMessages", messages: [record("persisted-uuid")] });
  expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).toBeChecked();
  host({ type: "replaceMessages", messages: [record("persisted-uuid", patch + " changed")] });
  expect(screen.getByRole("checkbox", { name: "Viewed a.ts" })).not.toBeChecked();
});
it("reuses immutable card evidence during unrelated events and reads replacements freshly", () => {
  const card = record();
  const readDiff = vi.fn(() => patch);
  Object.defineProperty((card.metadata.diff_entries as DiffEntry[])[0], "unified_diff", { get: readDiff });
  const first = conversationChanges([card]); const reads = readDiff.mock.calls.length;
  expect(reads).toBeGreaterThan(0);
  const unrelated: ChatMsg = { role: "agent", type: "text", content: "tool result", timestamp: "now", metadata: {} };
  expect(conversationChanges([unrelated, card]).files[0].signature).toBe(first.files[0].signature);
  expect(readDiff).toHaveBeenCalledTimes(reads);
  const changed = record("different-id", patch + " changed");
  expect(conversationChanges([changed]).files[0].signature).not.toBe(first.files[0].signature);
  expect(conversationChanges([card, record("second-card")]).files[0].signature).not.toBe(first.files[0].signature);
});

it("stacks small conversation comparisons and keeps filename-first path markup with a single diff viewport", () => {
  const first = record(); const second = record("second"); (second.metadata.diff_entries as DiffEntry[])[0].path = "long/parent/folder/second.ts";
  const ui = render(<ChangesTool model={model([first, second])} />);
  expect(screen.getByRole("article", { name: "Changes to a.ts" })).toBeInTheDocument(); expect(screen.getByRole("article", { name: "Changes to long/parent/folder/second.ts" })).toBeInTheDocument();
  expect(screen.getByText("Recorded conversation edits · scroll to review all files")).toBeInTheDocument(); expect(ui.container.querySelectorAll(".review-diff-body")).toHaveLength(1);
  expect(ui.container.querySelector('.review-file-path bdi')).toHaveTextContent('a.ts'); expect(screen.getByLabelText('More actions for long/parent/folder/second.ts')).toBeInTheDocument();
});
