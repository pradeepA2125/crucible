import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import DesktopApp from "../desktop/DesktopApp";
import { DraftStore } from "../../../../desktop/src/draft-store";
import type { DraftPort } from "../drafts/types";
import { vscode } from "../vscodeApi";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const host = (data: unknown) => act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
describe.each(["desktop", "vscode"])("%s shared receipt state", kind => {
  it("retains the origin status across idle/generic input recovery and offers only GET receipt verification", () => {
    render(kind === "desktop" ? <DesktopApp /> : <App />);
    host({ type: "desktop/project", name: "project" });
    host({ type: "renderThreadList", threads: [{ threadId: "A", title: "A", createdAt: "2026-10-23T00:00:00Z" }], activeThreadId: "A" });
    host({ type: "clearThread", threadId: "A" });
    if (kind === "vscode") fireEvent.click(screen.getByText("A"));
    host({ type: "chatAdmission", admission: { threadId: "A", state: "unknown", checking: false } });
    host({ type: "liveStatus", threadId: "A", status: null, turnActive: false });
    host({ type: "setInputEnabled", enabled: true });
    expect(screen.getByText(/Receipt is unconfirmed/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Check receipt" })).toBeEnabled();
    const post = vi.mocked(vscode.postMessage); post.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Check receipt" }));
    expect(post).toHaveBeenCalledExactlyOnceWith({ type: "checkChatReceipt" });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    host({ type: "clearThread", threadId: "B" });
    host({ type: "liveStatus", threadId: "B", status: null, turnActive: false });
    expect(screen.getByText(/Receipt is unconfirmed/)).toBeVisible();
    host({ type: "chatAdmission", admission: { threadId: "A", state: "unknown", checking: true } });
    expect(screen.getByRole("button", { name: "Checking receipt…" })).toBeDisabled();
    host({ type: "chatAdmission", admission: null }); host({ type: "setInputEnabled", enabled: true });
    expect(screen.queryByText(/Receipt is unconfirmed/)).toBeNull();
  });
  it("offers Check receipt from initial/reopened history or loading, before any live success", () => {
    render(kind === "desktop" ? <DesktopApp /> : <App />);
    host({ type: "chatAdmission", admission: { threadId: "origin", state: "unknown", checking: false } });
    expect(screen.getByRole("button", { name: "Check receipt" })).toBeVisible();
    host({ type: "renderThreadList", threads: [{ threadId: "origin", title: "Origin", createdAt: "2026-10-23T00:00:00Z" }, { threadId: "other", title: "Other", createdAt: "2026-10-23T00:00:00Z" }], activeThreadId: "origin" });
    host({ type: "clearThread", threadId: "origin" });
    expect(screen.getByRole("button", { name: "Check receipt" })).toBeEnabled();
    const post = vi.mocked(vscode.postMessage); post.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Check receipt" }));
    expect(post).toHaveBeenCalledExactlyOnceWith({ type: "checkChatReceipt" });
    if (kind === "desktop") expect(screen.getByRole("button", { name: "New conversation" })).toBeDisabled();
    else expect(screen.getByRole("button", { name: "New Chat" })).toBeDisabled();
    host({ type: "chatAdmission", admission: null }); host({ type: "setInputEnabled", enabled: true });
    expect(screen.queryByRole("button", { name: "Check receipt" })).toBeNull();
  });

});

it("preserves the existing root approval question across thread/history transitions", () => {
  render(<App />);
  host({ type: "renderThreadList", threads: [{ threadId: "A", title: "Approval origin", createdAt: "2026-10-23T00:00:00Z" }], activeThreadId: "A" });
  host({ type: "clearThread", threadId: "A" });
  fireEvent.click(screen.getByText("Approval origin"));
  host({ type: "confirmFullAccessFirstSend", requestId: "question", workspace: "project" });
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Back to history" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  const post = vi.mocked(vscode.postMessage); post.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(post).toHaveBeenCalledWith({ type: "fullAccessFirstSendChoice", requestId: "question", choice: "cancel" });
});


it("reuses retained composer drafts in the VS Code root and flushes before Back unmounts it", async () => {
  const store = new DraftStore();
  const image = { name: "kept.png", mediaType: "image/png", dataUrl: "data:image/png;base64,AA==", bytes: 1 };
  store.save("A", 1, { text: "original @sample.txt", imageAttachments: [image], mentionedPaths: ["sample.txt"] });
  let save = async () => {};
  const port: DraftPort = {
    read: async id => store.read(id), save: async (id, revision, value) => store.save(id, revision, value),
    begin: async (id, revision, value, draftId) => store.begin(id, revision, value, draftId),
    choose: async (id, revision, version, value, choice) => store.choose(id, revision, version, value, choice),
    onChange: () => () => {}, onFlush: callback => { save = callback; return () => { save = async () => {}; }; },
    flush: () => save(),
  };
  render(<App draftStore={port} />);
  host({ type: "renderThreadList", threads: [{ threadId: "A", title: "Draft origin", createdAt: "2026-10-23T00:00:00Z" }], activeThreadId: "A" });
  host({ type: "clearThread", threadId: "A" }); fireEvent.click(screen.getByText("Draft origin"));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Chat input" })).toHaveValue("original @sample.txt"));
  fireEvent.change(screen.getByRole("textbox", { name: "Chat input" }), { target: { value: "last instant @sample.txt" } });
  fireEvent.click(screen.getByRole("button", { name: "Back to history" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "New Chat" })).toBeVisible());
  expect(store.read("A").draft).toEqual({ text: "last instant @sample.txt", imageAttachments: [image], mentionedPaths: ["sample.txt"] });
  fireEvent.click(screen.getByText("Draft origin"));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Chat input" })).toHaveValue("last instant @sample.txt"));
  expect(screen.getByAltText("kept.png")).toBeVisible();
  port.flush = async () => { throw new Error("Draft save failed"); };
  fireEvent.click(screen.getByRole("button", { name: "Back to history" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Draft save failed"));
  expect(screen.getByRole("textbox", { name: "Chat input" })).toHaveValue("last instant @sample.txt");
});


it.each(["pending", "active", "attachment", "same-ID clear", "A→B→A"])("Back does not unmount the composer when %s changes during its flush", async kind => {
  let finish!: () => void;
  const port = { flush: () => new Promise<void>(resolve => { finish = resolve; }),
    read: async (threadId: string) => ({ threadId, revision: 0, version: 0, draft: { text: "", imageAttachments: [], mentionedPaths: [] } }),
    save: vi.fn(), begin: vi.fn(), choose: vi.fn(), onChange: () => () => {}, onFlush: () => () => {} } as DraftPort;
  render(<App draftStore={port} />);
  host({ type: "renderThreadList", threads: [{ threadId: "A", title: "Origin", createdAt: "2026-10-23T00:00:00Z" }], activeThreadId: "A" });
  host({ type: "clearThread", threadId: "A" }); fireEvent.click(screen.getByText("Origin"));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Chat input" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Back to history" }));
  if (kind === "pending") host({ type: "chatAdmission", admission: { threadId: "A", state: "pending", checking: false } });
  else if (kind === "active") host({ type: "liveStatus", threadId: "A", status: null, turnActive: true });
  else if (kind === "same-ID clear") host({ type: "clearThread", threadId: "A" });
  else if (kind === "A→B→A") act(() => {
    // One React batch deliberately has the same final thread ID as the origin.
    window.dispatchEvent(new MessageEvent("message", { data: { type: "clearThread", threadId: "B" } }));
    window.dispatchEvent(new MessageEvent("message", { data: { type: "clearThread", threadId: "A" } }));
  });
  else host({ type: "clearThread", threadId: "B" });
  await act(async () => finish());
  expect(screen.getByRole("textbox", { name: "Chat input" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New Chat" })).not.toBeInTheDocument();
});


it.each(["A→B→A", "pending", "active", "port replacement"])("Back's reducer guard rejects %s queued in the same async batch as flush completion", async kind => {
  let finish!: () => void;
  const port: DraftPort = { flush: () => new Promise<void>(resolve => { finish = resolve; }),
    read: async threadId => ({ threadId, revision: 0, version: 0, draft: { text: "", imageAttachments: [], mentionedPaths: [] } }),
    save: vi.fn(), begin: vi.fn(), choose: vi.fn(), onChange: () => () => {}, onFlush: () => () => {} };
  const ui = render(<App draftStore={port} />);
  host({ type: "renderThreadList", threads: [{ threadId: "A", title: "Batched origin", createdAt: "2026-10-23T00:00:00Z" }], activeThreadId: "A" });
  host({ type: "clearThread", threadId: "A" }); fireEvent.click(screen.getByText("Batched origin"));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Chat input" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Back to history" }));
  await act(async () => {
    const emit = (data: unknown) => window.dispatchEvent(new MessageEvent("message", { data }));
    if (kind === "A→B→A") { emit({ type: "clearThread", threadId: "B" }); emit({ type: "clearThread", threadId: "A" }); }
    else if (kind === "pending") emit({ type: "chatAdmission", admission: { threadId: "A", state: "pending", checking: false } });
    else if (kind === "active") emit({ type: "liveStatus", threadId: "A", status: null, turnActive: true });
    else ui.rerender(<App draftStore={{ ...port }} />);
    finish(); await Promise.resolve();
  });
  expect(screen.getByRole("textbox", { name: "Chat input" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New Chat" })).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("conversation changed while saving");
});
