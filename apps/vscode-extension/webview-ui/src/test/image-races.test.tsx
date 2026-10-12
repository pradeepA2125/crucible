import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InputArea } from "../components/InputArea";
import { UserMessage } from "../components/messages/UserMessage";
import { vscode } from "../vscodeApi";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const availability = { disabled: false, placeholder: "Message", showStop: false, taskStop: false };
class Reader {
  static all: Reader[] = [];
  result: string | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onloadend: (() => void) | null = null;
  abort = vi.fn();
  constructor() { Reader.all.push(this); }
  readAsDataURL() {}
  finish() { this.result = "data:image/png;base64,YQ=="; this.onload?.(); this.onloadend?.(); }
}
function Harness({ thread = "t1", initial = "" }: { thread?: string; initial?: string }) {
  const [draft, setDraft] = useState(initial);
  return <InputArea threadId={thread} availability={availability} draft={draft} onDraftChange={setDraft} />;
}
function attach() { fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["a"], "one.png", { type: "image/png" })] } }); }
function enter() { fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" }); }
function host(data: unknown) { act(() => { window.dispatchEvent(new MessageEvent("message", { data })); }); }
function calls(type: string) { return vi.mocked(vscode.postMessage).mock.calls.map(c => c[0] as any).filter(c => c.type === type); }
beforeEach(() => { vi.clearAllMocks(); Reader.all = []; vi.stubGlobal("FileReader", Reader); });
afterEach(() => vi.unstubAllGlobals());
describe("image asynchronous isolation", () => {
  it("blocks both send controls and Enter until all readers finish", () => {
    render(<Harness initial="hello" />); attach(); enter();
    expect(calls("sendMessage")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    act(() => Reader.all[0].finish()); enter();
    expect(calls("sendMessage")[0]).toMatchObject({ text: "hello", imageAttachments: [{ name: "one.png" }] });
  });
  it("cancels readers and discards late load after a thread switch", () => {
    const view = render(<Harness />); attach();
    view.rerender(<Harness thread="t2" />);
    expect(Reader.all[0].abort).toHaveBeenCalled();
    act(() => Reader.all[0].finish());
    expect(screen.queryByAltText("one.png")).toBeNull();
  });
  it("reports reader errors and clears pending state", () => {
    render(<Harness initial="hello" />); attach();
    act(() => { Reader.all[0].onerror?.(); Reader.all[0].onloadend?.(); });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not read one.png");
    expect(screen.getByRole("button", { name: "Send" })).not.toBeDisabled();
  });
  it("restores full failed draft when initial no-thread send adopts its thread", () => {
    const view = render(<Harness thread="" initial="hello" />); attach();
    act(() => Reader.all[0].finish()); enter();
    const sent = calls("sendMessage")[0];
    view.rerender(<Harness thread="new-thread" />);
    host({ type: "draftRecovery", draftId: sent.draftId, text: sent.text, imageAttachments: sent.imageAttachments });
    expect(screen.getByRole("textbox")).toHaveValue("hello");
    expect(screen.getByAltText("one.png")).toBeInTheDocument();
  });
  it("never overwrites a newer draft even if user types then erases it", () => {
    render(<Harness initial="hello" />); attach(); act(() => Reader.all[0].finish()); enter();
    const sent = calls("sendMessage")[0];
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "new" } });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
    host({ type: "draftRecovery", draftId: sent.draftId, text: sent.text, imageAttachments: sent.imageAttachments });
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.queryByAltText("one.png")).toBeNull();
  });
  it("ignores recovery in another thread", () => {
    const view = render(<Harness initial="hello" />); attach(); act(() => Reader.all[0].finish()); enter();
    const sent = calls("sendMessage")[0]; view.rerender(<Harness thread="t2" />);
    host({ type: "draftRecovery", draftId: sent.draftId, text: sent.text, imageAttachments: sent.imageAttachments });
    expect(screen.getByRole("textbox")).toHaveValue("");
  });
  it("preserves images in matching slash fallback, rejects old replies", () => {
    render(<Harness initial="/unknown hello" />); attach(); act(() => Reader.all[0].finish()); enter();
    const first = calls("expandPrompt")[0]; enter(); const second = calls("expandPrompt")[1];
    host({ type: "promptExpanded", requestId: first.requestId, found: false });
    expect(calls("sendMessage")).toHaveLength(0);
    host({ type: "promptExpanded", requestId: second.requestId, found: false });
    expect(calls("sendMessage")[0].imageAttachments).toHaveLength(1);
  });
  it("discards slash reply after changing away and back to identical text", () => {
    render(<Harness initial="/unknown hello" />); enter(); const request = calls("expandPrompt")[0];
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "different" } });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "/unknown hello" } });
    host({ type: "promptExpanded", requestId: request.requestId, found: true, text: "wrong" });
    expect(screen.getByRole("textbox")).toHaveValue("/unknown hello");
  });
  it("does not erase slash text when attachment conversion starts before reply", () => {
    render(<Harness initial="/unknown hello" />); enter(); const request = calls("expandPrompt")[0];
    attach(); host({ type: "promptExpanded", requestId: request.requestId, found: false });
    expect(screen.getByRole("textbox")).toHaveValue("/unknown hello");
    expect(calls("sendMessage")).toHaveLength(0);
  });
  it("accepts deliberate image prefill only for its current thread", () => {
    render(<Harness />);
    host({ type: "composerPrefill", threadId: "other", imageAttachments: [{ name: "one.png", dataUrl: "data:image/png;base64,YQ==" }] });
    expect(screen.queryByAltText("one.png")).toBeNull();
    host({ type: "composerPrefill", threadId: "t1", imageAttachments: [{ name: "one.png", mediaType: "image/png", dataUrl: "data:image/png;base64,YQ==", bytes: 1 }] });
    expect(screen.getByAltText("one.png")).toBeInTheDocument();
  });
  it("removes a draft preview", () => {
    render(<Harness />); attach(); act(() => Reader.all[0].finish());
    fireEvent.click(screen.getByRole("button", { name: "Remove one.png" }));
    expect(screen.queryByAltText("one.png")).toBeNull();
  });
  it("renders sent previews but does not load arbitrary preview URLs", () => {
    render(<UserMessage content="hello" images={[{ name: "safe", dataUrl: "data:image/png;base64,YQ==" }, { name: "missing" }, { name: "external", dataUrl: "https://evil.invalid/a.png" }]} />);
    expect(screen.getByAltText("safe")).toHaveAttribute("src", "data:image/png;base64,YQ==");
    expect(screen.getByText("missing: image preview unavailable")).toBeInTheDocument();
    expect(screen.queryByAltText("external")).toBeNull();
  });
});
