import { useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InputArea } from "../components/InputArea";
import { DraftStore } from "../../../../desktop/src/draft-store";
import type { Draft, DraftPort, DraftSnapshot } from "../drafts/types";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const data = (text: string): Draft => ({ text, imageAttachments: [], mentionedPaths: [] });
function fixture() {
  const store = new DraftStore(), listeners = new Set<(snapshot: DraftSnapshot) => void>();
  let flush = async () => {};
  const port: DraftPort = {
    read: vi.fn(async id => store.read(id)), save: vi.fn(async (id, revision, draft) => store.save(id, revision, draft)),
    begin: vi.fn(async (id, revision, draft, draftId) => store.begin(id, revision, draft, draftId)),
    choose: vi.fn(async (id, revision, version, current, choice) => store.choose(id, revision, version, current, choice)),
    onChange: listener => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    onFlush: callback => { flush = callback; return () => { flush = async () => {}; }; },
  };
  return { store, port, flush: () => flush(), changed: (snapshot?: DraftSnapshot) => { if (snapshot) for (const listener of listeners) listener(snapshot); } };
}
function Composer({ port, threadId = "a" }: { port: DraftPort; threadId?: string }) {
  const [text, setText] = useState("");
  return <InputArea threadId={threadId} draftStore={port} draft={text} onDraftChange={setText}
    availability={{ disabled: false, placeholder: "Message", showStop: false, taskStop: false }} />;
}
let post: ReturnType<typeof vi.fn>;
beforeEach(async () => { post = (await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>; post.mockClear(); });
describe("opt-in retained composer", () => {
  it("flushes edits within the debounce, retains images/mentions across same-instance switches and separates idle threads", async () => {
    const f = fixture();
    const image = { name: "diagram.png", mediaType: "image/png", dataUrl: "data:image/png;base64,AA==", bytes: 1 };
    f.store.save("a", 1, { text: "draft @sample.txt", imageAttachments: [image], mentionedPaths: ["sample.txt"] }); f.store.save("b", 1, data("other draft"));
    const view = render(<Composer port={f.port} />);
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("draft @sample.txt"));
    expect(screen.getByAltText("diagram.png")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "latest @sample.txt" } });
    await act(() => f.flush()); expect(f.store.read("a").draft).toMatchObject({ text: "latest @sample.txt", mentionedPaths: ["sample.txt"], imageAttachments: [image] });
    view.rerender(<Composer port={f.port} threadId="b" />); await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("other draft"));
    expect(screen.queryByAltText("diagram.png")).toBeNull();
    view.rerender(<Composer port={f.port} />); await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("latest @sample.txt"));
    expect(screen.getByAltText("diagram.png")).toBeInTheDocument();
  });
  it("never overwrites edits typed while hydration is delayed", async () => {
    const f = fixture(); let hydrate!: (snapshot: DraftSnapshot) => void;
    vi.mocked(f.port.read).mockImplementation(() => new Promise(resolve => { hydrate = resolve; }));
    render(<Composer port={f.port} />); fireEvent.change(screen.getByRole("textbox"), { target: { value: "typed first" } });
    await act(async () => { hydrate({ threadId: "a", revision: 9, version: 0, draft: data("old stored") }); });
    expect(screen.getByRole("textbox")).toHaveValue("typed first"); await act(() => f.flush());
    expect(f.store.read("a").draft.text).toBe("typed first"); expect(f.store.read("a").revision).toBeGreaterThan(9);
  });
  it("preserves newer text on refusal, offers restore/discard, and auto-restores an empty composer", async () => {
    const f = fixture(); f.store.save("a", 1, data("send original")); render(<Composer port={f.port} />);
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("send original"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(expect.objectContaining({ type: "sendMessage", text: "send original" })));
    const sent = post.mock.calls.map(([message]) => message).find(message => message.type === "sendMessage");
    expect(f.store.read("a").draft.text).toBe("");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "newer text" } }); await act(() => f.flush());
    act(() => f.changed(f.store.reject(sent.draftId)));
    expect(screen.getByRole("textbox")).toHaveValue("newer text");
    fireEvent.click(screen.getByRole("button", { name: "Swap in" }));
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("send original"));
    await act(() => f.flush()); expect(f.store.read("a").returned?.draft.text).toBe("newer text");
    fireEvent.click(screen.getByRole("button", { name: "Discard other draft" }));
    await waitFor(() => expect(f.store.read("a").returned).toBeUndefined());
    fireEvent.click(screen.getByRole("button", { name: "Send" })); await waitFor(() => expect(post.mock.calls.filter(([message]) => message.type === "sendMessage")).toHaveLength(2));
    const second = post.mock.calls.map(([message]) => message).filter(message => message.type === "sendMessage")[1];
    act(() => f.changed(f.store.reject(second.draftId)));
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("send original"));
  });
  it("waits for draft admission, blocks duplicate sends and keeps text typed during that wait", async () => {
    const f = fixture(); let release!: () => void;
    vi.mocked(f.port.begin).mockImplementation((id, revision, draft, draftId) => new Promise(resolve => { release = () => resolve(f.store.begin(id, revision, draft, draftId)); }));
    f.store.save("a", 1, data("original")); render(<Composer port={f.port} />); await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("original"));
    fireEvent.click(screen.getByRole("button", { name: "Send" })); await waitFor(() => expect(f.port.begin).toHaveBeenCalledOnce());
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(post.mock.calls.some(([message]) => message.type === "sendMessage")).toBe(false);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "next draft" } });
    await act(async () => release()); await waitFor(() => expect(post).toHaveBeenCalledWith(expect.objectContaining({ type: "sendMessage", text: "original" })));
    expect(screen.getByRole("textbox")).toHaveValue("next draft");
    expect(f.store.read("a").draft.text).toBe("next draft"); expect(f.port.begin).toHaveBeenCalledOnce();
  });
  it("keeps newer unsaved text when a swap fails and retries without losing either draft", async () => {
    const f = fixture(); f.store.save("a", 1, data("sent message"));
    f.store.begin("a", 2, data("sent message"), "send");
    render(<Composer port={f.port} />); await waitFor(() => expect(f.port.read).toHaveBeenCalled());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "newer unsaved text" } });
    act(() => f.changed(f.store.reject("send")));
    vi.mocked(f.port.choose).mockRejectedValueOnce(new Error("Retry swap"));
    fireEvent.click(screen.getByRole("button", { name: "Swap in" }));
    await screen.findByText("Retry swap");
    expect(screen.getByRole("textbox")).toHaveValue("newer unsaved text");
    expect(screen.getByRole("group", { name: "Other unsent draft" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toHaveAttribute("aria-describedby");
    fireEvent.click(screen.getByRole("button", { name: "Swap in" }));
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("sent message"));
    expect(f.store.read("a").returned?.draft.text).toBe("newer unsaved text");
  });

  it("reconciles an admitted swap when its reply is lost instead of duplicating the draft on retry", async () => {
    const f = fixture(); f.store.save("a", 1, data("original")); f.store.begin("a", 2, data("original"), "send");
    render(<Composer port={f.port} />); await waitFor(() => expect(f.port.read).toHaveBeenCalled());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "newer" } }); act(() => f.changed(f.store.reject("send")));
    vi.mocked(f.port.choose).mockImplementationOnce(async (...args) => { f.store.choose(...args); throw new Error("Reply timed out"); });
    fireEvent.click(screen.getByRole("button", { name: "Swap in" }));
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("original"));
    expect(f.store.read("a").returned?.draft.text).toBe("newer");
    fireEvent.click(screen.getByRole("button", { name: "Swap in" }));
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("newer"));
    expect(f.store.read("a").returned?.draft.text).toBe("original");
  });

});
