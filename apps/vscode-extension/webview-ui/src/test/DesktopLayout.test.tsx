import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DesktopApp from "../desktop/DesktopApp";
import { createLayoutClient, type LayoutPort } from "../desktop/layoutClient";
import { createDraftClient } from "../desktop/draftClient";
import { DEFAULT_LAYOUT, type DesktopLayout } from "../../../../desktop/src/layout";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const host = (data: unknown) => act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
const ready = (count = 2, active = "0") => {
  host({ type: "renderThreadList", threads: Array.from({ length: count }, (_, id) => ({ threadId: String(id), workspacePath: "/project", title: `Conversation ${id}`, createdAt: "2026-10-21T10:00:00Z" })), activeThreadId: active });
  host({ type: "clearThread", threadId: active }); host({ type: "liveStatus", threadId: active, status: null, turnActive: false, agentsRunning: 0 });
};
function fixture(value = DEFAULT_LAYOUT) {
  let flush = async () => {};
  const port: LayoutPort = { read: vi.fn(async () => value), save: vi.fn(async () => {}), onFlush: callback => { flush = callback; return () => { flush = async () => {}; }; } };
  return { port, flush: () => flush() };
}
beforeEach(() => {
  window.innerWidth = 1440;
  class TestPointer extends MouseEvent { readonly pointerId = 1; }
  vi.stubGlobal("PointerEvent", TestPointer);
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
describe("adjustable desktop layout", () => {
  it("commits a drag only on release, preserves the mounted composer, cancels on Escape and resets from the keyboard", async () => {
    const f = fixture(); render(<DesktopApp layoutStore={f.port} />); ready();
    await waitFor(() => expect(f.port.read).toHaveBeenCalled());
    const input = screen.getByRole("textbox", { name: "Chat input" }); fireEvent.change(input, { target: { value: "keep my draft" } });
    const divider = screen.getByRole("separator", { name: "Resize conversations" }), shell = divider.closest(".desktop-shell") as HTMLElement;
    fireEvent.pointerDown(divider, { clientX: 248, button: 0 }); fireEvent.pointerMove(divider, { clientX: 348 });
    expect(f.port.save).not.toHaveBeenCalled(); fireEvent.pointerUp(divider, { clientX: 348 }); await act(() => f.flush());
    expect(f.port.save).toHaveBeenLastCalledWith(expect.objectContaining({ sidebarWidth: 348 }));
    expect(shell.style.getPropertyValue("--desktop-sidebar-width")).toBe("348px");
    expect(screen.getByRole("textbox", { name: "Chat input" })).toBe(input); expect(input).toHaveValue("keep my draft");
    fireEvent.pointerDown(divider, { clientX: 348, button: 0 }); fireEvent.pointerMove(divider, { clientX: 410 }); fireEvent.keyDown(divider, { key: "Escape" });
    expect(shell.style.getPropertyValue("--desktop-sidebar-width")).toBe("348px"); expect(document.body.style.userSelect).not.toBe("none");
    fireEvent.keyDown(divider, { key: "Enter" }); await act(() => f.flush()); expect(divider).toHaveAttribute("aria-valuenow", "248");
    fireEvent.click(screen.getByRole("button", { name: "Hide conversations" })); fireEvent.click(screen.getByRole("button", { name: "Show conversations" }));
    expect(screen.getByRole("textbox", { name: "Chat input" })).toBe(input); expect(input).toHaveValue("keep my draft");
  });
  it("uses the release position when a native drag omits its final move event", async () => {
    const f = fixture(); render(<DesktopApp layoutStore={f.port} />); ready();
    await waitFor(() => expect(f.port.read).toHaveBeenCalled());
    const divider = screen.getByRole("separator", { name: "Resize conversations" });
    fireEvent.pointerDown(divider, { clientX: 248, button: 0 });
    fireEvent.pointerMove(divider, { clientX: 250 });
    fireEvent.pointerUp(divider, { clientX: 348 }); await act(() => f.flush());
    expect(f.port.save).toHaveBeenLastCalledWith(expect.objectContaining({ sidebarWidth: 348 }));
    expect(document.body.style.userSelect).not.toBe("none");
  });
  it("never lets delayed preference hydration overwrite a width the user already changed", async () => {
    const f = fixture(); let hydrate!: (value: DesktopLayout) => void;
    vi.mocked(f.port.read).mockImplementation(() => new Promise(resolve => { hydrate = resolve; }));
    render(<DesktopApp layoutStore={f.port} />); ready(); await waitFor(() => expect(f.port.read).toHaveBeenCalled());
    fireEvent.keyDown(screen.getByRole("separator"), { key: "ArrowRight" });
    await act(async () => { hydrate({ ...DEFAULT_LAYOUT, sidebarWidth: 400, groupExpanded: false }); });
    expect(screen.getByRole("separator")).toHaveAttribute("aria-valuenow", "264");
    expect(screen.getByRole("button", { name: /Project 2/ })).toHaveAttribute("aria-expanded", "false");
    await act(() => f.flush()); expect(f.port.save).toHaveBeenLastCalledWith(expect.objectContaining({ sidebarWidth: 264, groupExpanded: false }));
  });
  it("always shows the active older thread, expands long lists and ignores the limit during filtering", () => {
    render(<DesktopApp />); ready(12, "11");
    expect(screen.getByRole("button", { name: /Conversation 11/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Conversation 9 / })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 3 more" })); expect(screen.getByRole("button", { name: /Conversation 9 / })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show less" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Search conversations" }), { target: { value: "Conversation" } });
    expect(screen.getByRole("button", { name: /Conversation 9 / })).toBeInTheDocument();
  });
  it("blocks the native flush until the layout acknowledgement arrives and propagates a save error", async () => {
    let saved!: () => void;
    const f = fixture(); vi.mocked(f.port.save).mockImplementation(() => new Promise(resolve => { saved = resolve; }));
    render(<DesktopApp layoutStore={f.port} />); ready(); await waitFor(() => expect(f.port.read).toHaveBeenCalled());
    fireEvent.keyDown(screen.getByRole("separator"), { key: "End" });
    let done = false;
    await act(async () => {
      const pending = f.flush().then(() => { done = true; }); await Promise.resolve(); await Promise.resolve();
      expect(done).toBe(false); saved(); await pending;
    });
    expect(done).toBe(true);
    vi.mocked(f.port.save).mockRejectedValueOnce(new Error("Disk unavailable"));
    fireEvent.keyDown(screen.getByRole("separator"), { key: "Home" }); await act(async () => { await expect(f.flush()).rejects.toThrow("Disk unavailable"); });
  });
  it("correlates layout replies and makes native draft flush wait for the layout flush hook", async () => {
    const send = vi.fn(), layout = createLayoutClient(send), pending = layout.read(); const read = send.mock.calls.at(-1)?.[0];
    host({ type: "desktop/layoutResult", requestId: "other", layout: { ...DEFAULT_LAYOUT, sidebarWidth: 400 } });
    host({ type: "desktop/layoutResult", requestId: read.requestId, layout: DEFAULT_LAYOUT }); expect(await pending).toEqual(DEFAULT_LAYOUT);
    let release!: () => void; const barrier = new Promise<void>(resolve => { release = resolve; });
    layout.onFlush?.(() => barrier); const drafts = createDraftClient(send, window, () => layout.flush());
    host({ type: "draft/flush", requestId: "native" }); await Promise.resolve();
    expect(send.mock.calls.some(([message]) => message.type === "draft/flushAck")).toBe(false);
    release(); await waitFor(() => expect(send).toHaveBeenCalledWith({ type: "draft/flushAck", requestId: "native" })); drafts.dispose();
  });
  it("retries a failed preference read and merges only the changed field before saving", async () => {
    const stored = { ...DEFAULT_LAYOUT, sidebarWidth: 390, workspaceWidth: 800, groupExpanded: false }, f = fixture(stored);
    vi.mocked(f.port.read).mockRejectedValueOnce(new Error("Read timed out"));
    render(<DesktopApp layoutStore={f.port} />); ready(); await screen.findByText("Read timed out");
    expect(f.port.save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Hide conversations" })); await act(() => f.flush());
    expect(f.port.read).toHaveBeenCalledTimes(2);
    expect(f.port.save).toHaveBeenLastCalledWith({ ...stored, sidebarVisible: false });
  });
  it("acknowledges native content flush despite a failed cosmetic save", async () => {
    const f = fixture(), send = vi.fn(), layout = createLayoutClient(send);
    vi.mocked(f.port.save).mockRejectedValue(new Error("Disk unavailable"));
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(<DesktopApp layoutStore={f.port} />); ready(); await waitFor(() => expect(f.port.read).toHaveBeenCalled());
    layout.onFlush?.(f.flush); const drafts = createDraftClient(send, window, () => layout.flush());
    fireEvent.keyDown(screen.getByRole("separator"), { key: "End" }); host({ type: "draft/flush", requestId: "native-failure" });
    await waitFor(() => expect(send).toHaveBeenCalledWith({ type: "draft/flushAck", requestId: "native-failure" }));
    expect(screen.getByText("Disk unavailable")).toBeInTheDocument(); expect(warning).toHaveBeenCalled();
    drafts.dispose(); warning.mockRestore();
  });
  it("caps a hung cosmetic flush so it cannot block content/navigation", async () => {
    vi.useFakeTimers(); const layout = createLayoutClient(vi.fn()); layout.onFlush?.(() => new Promise(() => {}));
    let done = false; const pending = layout.flush().then(() => { done = true; });
    await vi.advanceTimersByTimeAsync(250); await pending; expect(done).toBe(true);
  });

  it("resizes inspection without remounting it, switches to full area at the actual fit boundary and restores preferences on a wider window", async () => {
    const preferred = { ...DEFAULT_LAYOUT, sidebarWidth: 390, workspaceWidth: 800 }, f = fixture(preferred);
    render(<DesktopApp layoutStore={f.port} />); ready(); await waitFor(() => expect(screen.getByRole("separator")).toHaveAttribute("aria-valuenow", "390"));
    const input = screen.getByRole("textbox", { name: "Chat input" }); fireEvent.change(input, { target: { value: "split draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Open workspace" }));
    const inspection = screen.getByRole("complementary", { name: "Workspace" }), divider = screen.getByRole("separator", { name: "Resize workspace" });
    expect(divider).toHaveAttribute("aria-valuenow", "618");
    fireEvent.keyDown(divider, { key: "ArrowRight" }); await act(() => f.flush());
    expect(f.port.save).toHaveBeenLastCalledWith(expect.objectContaining({ workspaceWidth: 602 }));
    expect(screen.getByRole("complementary", { name: "Workspace" })).toBe(inspection);
    vi.mocked(f.port.save).mockClear();
    act(() => { window.innerWidth = 900; window.dispatchEvent(new Event("resize")); });
    expect(screen.queryByRole("separator", { name: "Resize workspace" })).toBeNull();
    expect(screen.getByRole("complementary", { name: "Workspace" })).toBe(inspection); expect(input).toHaveValue("split draft");
    expect(f.port.save).not.toHaveBeenCalled();
    act(() => { window.innerWidth = 1600; window.dispatchEvent(new Event("resize")); });
    expect(screen.getByRole("separator", { name: "Resize workspace" })).toHaveAttribute("aria-valuenow", "602");
    expect(screen.getByRole("textbox", { name: "Chat input" })).toBe(input); expect(input).toHaveValue("split draft");
    expect(f.port.save).not.toHaveBeenCalled();
  });

});
