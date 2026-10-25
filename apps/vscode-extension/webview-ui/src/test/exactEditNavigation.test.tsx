import { Profiler, useState } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ChangesTool, type ChangesModel, type ExactEditSelection } from "../desktop/ChangesTool";
import { conversationChanges, recordedEditTarget } from "../desktop/conversationChanges";
import type { ChatMsg, DiffEntry } from "../types";
const contexts = vi.hoisted(() => [] as unknown[]);
vi.mock("../components/ThreadView", async importOriginal => {
  const actual = await importOriginal<typeof import("../components/ThreadView")>();
  const { useContext } = await import("react"), { ChangesRoute } = await import("../components/activity/changesRoute");
  return { ...actual, ThreadView: (props: Parameters<typeof actual.ThreadView>[0]) => { contexts.push(useContext(ChangesRoute)); return <actual.ThreadView {...props} />; } };
});
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
import DesktopApp from "../desktop/DesktopApp";
afterEach(() => { vi.restoreAllMocks(); contexts.length = 0; });
const patch = "@@ -1 +1 @@\n-before\n+after";
const card = (index: number, long = false, path = "a.ts"): ChatMsg => ({ role: "agent", type: "diff_card", content: "", timestamp: "now", metadata: { resolved: "applied", diff_entries: [{ path, additions: 1, deletions: 1, unified_diff: long ? "@@ -1 +1 @@\n" + `+edit${index}\n`.repeat(399) : patch }] } });
const targetFor = (messages: ChatMsg[], index: number, request = 1): ExactEditSelection => ({ ...recordedEditTarget(messages, { message: messages[index], position: 0 }, ((messages[index].metadata.diff_entries as DiffEntry[])[0]).path)!, request });
const model = (messages: ChatMsg[], target?: ExactEditSelection): ChangesModel => ({ changes: conversationChanges(messages), target, selected: target?.path, select: vi.fn(), isViewed: () => false, setViewed: vi.fn() });
const host = (data: unknown) => act(() => window.dispatchEvent(new MessageEvent("message", { data })));
function ready(id = "a") {
  host({ type: "renderThreadList", activeThreadId: id, threads: ["a", "b"].map(threadId => ({ threadId, title: `Conversation ${threadId}`, createdAt: "now" })) });
  host({ type: "clearThread", threadId: id }); host({ type: "liveStatus", threadId: id, turnActive: false, agentsRunning: 0 });
}
it("renders the target's third page in the first commit, preserving it on live append and allowing explicit pagination/repeated routes", () => {
  const messages = Array.from({ length: 13 }, (_, i) => card(i, true)), target = targetFor(messages, 12), commits: string[] = [];
  const draw = (value: ChangesModel) => <Profiler id="pages" onRender={() => commits.push(document.querySelector('[aria-label="Recorded edits pages"]')?.textContent ?? "")}><ChangesTool model={value} /></Profiler>;
  const ui = render(draw(model(messages, target)));
  expect(commits[0]).toContain("Edits 11–13 of 13"); expect(screen.getByRole("region", { name: "Edit 13 of 13" })).toHaveAttribute("aria-current", "true");
  const appended = [...messages, card(13, true)]; ui.rerender(draw(model(appended, target)));
  expect(screen.getByText("Edits 11–14 of 14")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Previous edits" })); expect(screen.getByText("Edits 6–10 of 14")).toBeInTheDocument();
  ui.rerender(draw(model(appended, { ...target, request: 2 })));
  expect(screen.getByText("Edits 11–14 of 14")).toBeInTheDocument();
  expect(screen.getByText(/Recorded edit to a.ts/)).toBeInTheDocument();
  expect(screen.getByText(/This turn made 14 edits/)).toBeInTheDocument();
});
it("keeps same-byte reconciliation selected and refuses a reused ordinal with changed bytes, with an explicit file-review escape", () => {
  const original = card(0), target = targetFor([original], 0), ui = render(<ChangesTool model={model([original], target)} />);
  ui.rerender(<ChangesTool model={model([{ ...original, id: "persisted-id" }], target)} />);
  expect(screen.getByRole("region", { name: "Edit 1 of 1" })).toHaveAttribute("aria-current", "true");
  const changed = card(1, true), next = model([changed], target); ui.rerender(<ChangesTool model={next} />);
  expect(screen.getByText(/This recorded edit is unavailable/)).toBeInTheDocument(); expect(screen.queryByRole("region", { name: "Edit 1 of 1" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Review available edits to a.ts" })); expect(next.select).toHaveBeenCalledWith("a.ts");
  ui.rerender(<ChangesTool model={model([changed])} />); expect(screen.getByRole("region", { name: "Edit 1 of 1" })).not.toHaveAttribute("aria-current");
});
it("does not substitute another file when the selected record is outside the listed evidence", () => {
  const selected = card(0), target = targetFor([selected], 0);
  render(<ChangesTool model={model([card(1, false, "b.ts")], target)} />);
  expect(screen.getByText(/This recorded edit is unavailable/)).toBeInTheDocument(); expect(screen.queryByRole("article")).toBeNull();
  expect(screen.queryByRole("button", { name: /Review available edits/ })).toBeNull();
});
it("selects the latest missing-patch record, explains turn totals, and leaves pending/discarded cards out", () => {
  const first = card(0), last = card(1); delete (last.metadata.diff_entries as DiffEntry[])[0].unified_diff;
  const pending = card(2), discarded = card(3); delete pending.metadata.resolved; discarded.metadata.resolved = "discarded";
  const messages = [first, last, pending, discarded], target = targetFor(messages, 1); render(<ChangesTool model={model(messages, target)} />);
  expect(screen.getByRole("region", { name: "Edit 2 of 2" })).toHaveAttribute("aria-current", "true");
  expect(screen.getByText("No diff text was recorded for this edit.")).toBeInTheDocument();
  expect(screen.getByText(/Last edit to a.ts in this turn · edit 2 of 2/)).toBeInTheDocument(); expect(screen.getByText(/This turn made 2 edits/)).toBeInTheDocument();
});
function mockGeometry() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains("review-diff-body")) return { top: 100, height: 700 } as DOMRect;
    if (this.classList.contains("review-file-header")) return { top: 100, height: 42 } as DOMRect;
    if (this.classList.contains("review-recorded-edit")) return { top: 500, bottom: 700, height: 200 } as DOMRect;
    return { top: 100, bottom: 900, height: 800 } as DOMRect;
  });
}
it("scrolls an already open continuous comparison to the requested record, offsets its sticky header and repeats without moving focus", () => {
  mockGeometry(); const messages = [card(0), card(1, false, "b.ts")], target = targetFor(messages, 1);
  const ui = render(<><input aria-label="Draft" /><ChangesTool model={model(messages)} /></>), input = screen.getByRole("textbox", { name: "Draft" }); input.focus();
  const root = ui.container.querySelector(".review-diff-body")! as HTMLElement; root.scrollTop = 0;
  ui.rerender(<><input aria-label="Draft" /><ChangesTool model={model(messages, target)} /></>);
  expect(root.scrollTop).toBe(350); expect(input).toHaveFocus();
  root.scrollTop = 0; ui.rerender(<><input aria-label="Draft" /><ChangesTool model={model(messages, { ...target, request: 2 })} /></>);
  expect(root.scrollTop).toBe(350); expect(input).toHaveFocus();
});
it("scrolls in large single-file mode too", () => {
  mockGeometry(); const messages = Array.from({ length: 13 }, (_, i) => card(i, true));
  const ui = render(<ChangesTool model={model(messages, targetFor(messages, 12))} />);
  expect((ui.container.querySelector(".review-diff-body")! as HTMLElement).scrollTop).toBe(350);
});
it("scroll-following keeps the exact outline and explicit tree navigation clears it", () => {
  const messages = [card(0), card(1, false, "b.ts")], target = targetFor(messages, 0);
  function View({ items = messages }: { items?: ChatMsg[] }) {
    const [selection, setSelection] = useState<{ selected: string; target?: ExactEditSelection }>({ selected: "a.ts", target });
    return <ChangesTool model={{ ...model(items), ...selection, select: selected => setSelection({ selected }), highlight: selected => setSelection(value => ({ ...value, selected })) }} />;
  }
  const ui = render(<View />), root = ui.container.querySelector(".review-diff-body")! as HTMLElement;
  root.getBoundingClientRect = () => ({ top: 10 } as DOMRect);
  screen.getByRole("article", { name: "Changes to a.ts" }).getBoundingClientRect = () => ({ top: -900, bottom: -100 } as DOMRect);
  screen.getByRole("article", { name: "Changes to b.ts" }).getBoundingClientRect = () => ({ top: 10, bottom: 300 } as DOMRect);
  root.scrollTop = 200; fireEvent.scroll(root);
  expect(screen.getByText(/File 2 of 2: b.ts/)).toBeInTheDocument(); expect(ui.container.querySelectorAll('.review-recorded-edit[aria-current="true"]')).toHaveLength(1); expect(root.scrollTop).toBe(200);
  ui.rerender(<View items={[...messages, card(2, false, "b.ts")]} />);
  expect(root.scrollTop).toBe(200); expect(ui.container.querySelectorAll('.review-recorded-edit[aria-current="true"]')).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "b.ts" })); expect(ui.container.querySelector('.review-recorded-edit[aria-current="true"]')).toBeNull();
});
it("the provider stays identical across appends, routes latest records, preserves the shared draft and clears across threads", () => {
  window.innerWidth = 1440; const ui = render(<DesktopApp />); ready(); host({ type: "appendMessage", message: card(0) });
  const callback = contexts.at(-1), input = screen.getByRole("textbox", { name: "Chat input" }); fireEvent.change(input, { target: { value: "do not send or replace this draft" } }); input.focus();
  host({ type: "appendMessage", message: card(1) }); expect(contexts.at(-1)).toBe(callback);
  fireEvent.click(screen.getByRole("button", { name: "Show diff for a.ts in Changes" }));
  const changes = screen.getByRole("region", { name: "Changes" }); expect(within(changes).getByRole("region", { name: "Edit 2 of 2" })).toHaveAttribute("aria-current", "true"); expect(input).toHaveValue("do not send or replace this draft");
  host({ type: "replaceMessages", messages: [card(0), { ...card(1), id: "persisted-id" }] });
  expect(within(changes).getByRole("region", { name: "Edit 2 of 2" })).toHaveAttribute("aria-current", "true");
  ready("b"); expect(ui.container.querySelector('.review-recorded-edit[aria-current="true"]')).toBeNull(); expect(screen.getByText("No edits have been applied in this conversation.")).toBeInTheDocument();
});

it("a stale transcript route cannot match a same-path edit in a different active thread", () => {
  window.innerWidth = 1440; render(<DesktopApp />); ready(); const old = card(0); host({ type: "appendMessage", message: old });
  const staleRoute = contexts.at(-1) as (path: string, source: { message: ChatMsg; position: number }) => void;
  ready("b"); host({ type: "appendMessage", message: card(0) });
  act(() => staleRoute("a.ts", { message: old, position: 0 }));
  const review = screen.getByRole("region", { name: "Changes" });
  expect(within(review).getByText(/This recorded edit is unavailable/)).toBeInTheDocument(); expect(within(review).queryByRole("article")).toBeNull();
});
