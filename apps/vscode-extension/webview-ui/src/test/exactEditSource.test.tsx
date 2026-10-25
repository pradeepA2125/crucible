import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { groupTranscript, type FileChange } from "../transcript/groupTranscript";
import { conversationChanges, recordedEditTarget } from "../desktop/conversationChanges";
import { ChangesRoute } from "../components/activity/changesRoute";
import { TranscriptItems } from "../components/shared/TranscriptItems";
import { AgentWorkTranscript } from "../components/shared/AgentWorkTranscript";
import type { ChatMsg, DiffEntry } from "../types";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const patch = "@@ -1 +1 @@\n-before\n+after";
const user = (): ChatMsg => ({ role: "user", type: "text", content: "change", timestamp: "now", metadata: {} });
const card = (entries: Partial<DiffEntry>[] = [{ path: "a.ts", additions: 1, deletions: 1, unified_diff: patch }], resolved: string | undefined = "applied"): ChatMsg => ({ role: "agent", type: "diff_card", content: "", timestamp: "now", metadata: { diff_entries: entries, resolved } });
const changes = (messages: ChatMsg[], sources = true): FileChange[] => groupTranscript(messages, sources).flatMap(item => item.kind === "changes" ? item.files : []);
it("keeps default grouping provenance-free and records the last applied raw entry, including missing patch text", () => {
  const first = card(), last = card([{}, { path: "a.ts", additions: 2, deletions: 0 }]);
  const messages = [user(), first, last, card(undefined, "discarded")];
  const row = changes(messages)[0];
  expect(row).toMatchObject({ additions: 3, deletions: 1, diff: patch, editSource: { message: last, position: 1 } });
  expect(row.editSource!.message).toBe(last);
  expect(changes(messages, false)[0]).not.toHaveProperty("editSource");
  const target = recordedEditTarget(messages, row.editSource!, "a.ts")!;
  expect(target).toMatchObject({ key: "1:1:1", editsInTurn: 2, lastInTurn: true });
  const edit = conversationChanges(messages).files[0].edits.find(edit => edit.key === target.key)!;
  expect(edit.diff).toBeUndefined(); expect(edit.signature).toBe(target.signature);
});
it("separates identical patches by turn/card position while inserted tools and reconciliation keep the same evidence", () => {
  const first = card(), second = card(), last = card();
  const messages = [user(), first, second, user(), last];
  const firstTarget = recordedEditTarget(messages, { message: first, position: 0 }, "a.ts")!;
  expect(firstTarget).toMatchObject({ key: "1:0:0", editsInTurn: 2, lastInTurn: false });
  const target = recordedEditTarget(messages, { message: last, position: 0 }, "a.ts")!;
  expect(target.key).toBe("2:0:0"); expect(target.signature).toBe(firstTarget.signature);
  const tool: ChatMsg = { role: "agent", type: "text", content: "progress", timestamp: "now", metadata: { progress: true } };
  const persisted = { ...last, id: "persisted-id" }, reconciled = [...messages.slice(0, -1), tool, persisted];
  expect(recordedEditTarget(reconciled, { message: persisted, position: 0 }, "a.ts")).toEqual(target);
  expect(recordedEditTarget(reconciled, { message: last, position: 0 }, "a.ts")).toBeUndefined();
});
it("rejects foreign/removed, pending/discarded, wrong path and invalid positions without path or persisted-id fallback", () => {
  const valid = card(), pending = card([], undefined), discarded = card(undefined, "discarded"), messages = [valid, pending, discarded];
  delete pending.metadata.resolved;
  for (const message of [card(), pending, discarded, { ...valid, id: "same-id" }]) expect(recordedEditTarget(messages, { message, position: 0 }, "a.ts")).toBeUndefined();
  for (const position of [-1, 0.5, NaN, 1]) expect(recordedEditTarget(messages, { message: valid, position }, "a.ts")).toBeUndefined();
  expect(recordedEditTarget(messages, { message: valid, position: 0 }, "b.ts")).toBeUndefined();
  expect(recordedEditTarget([], { message: valid, position: 0 }, "a.ts")).toBeUndefined();
});
it("reused keys with changed bytes have different edit evidence but existing Viewed file hashes stay compatible", () => {
  const original = card(), replacement = card([{ path: "a.ts", additions: 1, deletions: 1, unified_diff: patch + "changed" }]);
  const a = recordedEditTarget([original], { message: original, position: 0 }, "a.ts")!, b = recordedEditTarget([replacement], { message: replacement, position: 0 }, "a.ts")!;
  expect(a.key).toBe(b.key); expect(a.signature).not.toBe(b.signature);
  expect(conversationChanges([original]).files[0].signature).not.toBe(conversationChanges([replacement]).files[0].signature);
});
it("only the main explicit opt-in routes source references and reconciled rows update their memoized source", () => {
  const route = vi.fn(), original = card(), persisted = { ...original, id: "persisted-id" };
  const ui = render(<ChangesRoute.Provider value={route}><TranscriptItems messages={[original]} recordEditSources /></ChangesRoute.Provider>);
  fireEvent.click(screen.getByRole("button", { name: "Show diff for a.ts in Changes" }));
  expect(route).toHaveBeenLastCalledWith("a.ts", { message: original, position: 0 });
  ui.rerender(<ChangesRoute.Provider value={route}><TranscriptItems messages={[persisted]} recordEditSources /></ChangesRoute.Provider>);
  fireEvent.click(screen.getByRole("button", { name: "Show diff for a.ts in Changes" }));
  expect(route.mock.lastCall![1].message).toBe(persisted);
  ui.rerender(<ChangesRoute.Provider value={route}><TranscriptItems messages={[persisted]} /></ChangesRoute.Provider>);
  fireEvent.click(screen.getByRole("button", { name: "Show diff for a.ts in Changes" })); expect(route).toHaveBeenLastCalledWith("a.ts");
});
it("child/team transcripts under the desktop route keep native open and their inline diff", () => {
  const route = vi.fn();
  render(<ChangesRoute.Provider value={route}><AgentWorkTranscript messages={[card()]} /></ChangesRoute.Provider>);
  expect(screen.queryByRole("button", { name: "Show diff for a.ts in Changes" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show diff for a.ts" }));
  expect(screen.getByText("after")).toBeInTheDocument(); expect(route).not.toHaveBeenCalled();
});
