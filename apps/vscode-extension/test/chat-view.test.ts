import { describe, expect, it, vi } from "vitest";
import { ChatView, type ChatViewPort } from "../src/chat-view.js";
import type { ComposerModelState } from "../src/chat-message-handlers.js";

function setup() {
  let open = true;
  let identity = {};
  let request = 0;
  const postMessage = vi.fn<ChatViewPort["postMessage"]>();
  const listModels = vi.fn<ChatViewPort["listModels"]>(async () => ({ current: null, options: ["new"] }));
  const view = new ChatView({ postMessage: message => { if (open) postMessage(message); },
    isOpen: () => open, identity: () => open ? identity : null,
    createRequestId: () => `question-${++request}`, listModels });
  return { view, postMessage, listModels,
    replace: () => { identity = {}; }, close: () => { open = false; } };
}

describe("shared retained conversation view", () => {
  it("replays approval, unanswered confirmations, context and workbar in order", async () => {
    const h = setup();
    const approval = { tier: "unknown", snapshot: null, mode: null, confirmed: false,
      pending: false, warningDismissed: false, trusted: true, error: null, notice: null } as const;
    h.view.renderApproval(approval);
    const answer = h.view.confirmFullAccessFirstSend("project");
    const context = { input_tokens: 100, context_window: 200000 };
    const workbar = { phaseLabel: "Exploring" };
    h.view.renderContextUsage(context, true); h.view.updateWorkbar(workbar);
    h.postMessage.mockClear();
    h.view.replayState();
    expect(h.postMessage.mock.calls).toEqual([
      [{ type: "approvalState", approval }],
      [{ type: "confirmFullAccessFirstSend", requestId: "question-1", workspace: "project" }],
      [{ type: "chatAdmission", admission: null }],
      [{ type: "renderContextUsage", usage: context, stale: true }],
      [{ type: "updateWorkbar", info: workbar }],
    ]);
    h.view.answerFullAccessQuestion("question-1", "accept_edits");
    await expect(answer).resolves.toBe("accept_edits");
    h.postMessage.mockClear(); h.view.replayState();
    expect(h.postMessage.mock.calls.some(([message]) => message.type === "confirmFullAccessFirstSend")).toBe(false);
  });

  it.each(["continue", "accept_edits", "cancel", "unknown", null])("normalizes a Full access answer without changing policy: %s", async choice => {
    const h = setup();
    const answer = h.view.confirmFullAccessFirstSend("project");
    h.view.answerFullAccessQuestion("not-a-question", "continue");
    h.view.answerFullAccessQuestion("question-1", choice);
    h.view.answerFullAccessQuestion("question-1", "continue");
    await expect(answer).resolves.toBe(choice === "continue" || choice === "accept_edits" ? choice : "cancel");
    expect(h.postMessage).toHaveBeenCalledOnce();
  });

  it("cancels all unanswered confirmations on disposal and never asks a closed view", async () => {
    const h = setup();
    const one = h.view.confirmFullAccessFirstSend("one");
    const two = h.view.confirmFullAccessFirstSend("two");
    h.close(); h.view.disposeViewState();
    await expect(one).resolves.toBe("cancel"); await expect(two).resolves.toBe("cancel");
    await expect(h.view.confirmFullAccessFirstSend("three")).resolves.toBe("cancel");
    h.view.answerFullAccessQuestion("question-1", "continue");
    expect(h.postMessage).toHaveBeenCalledTimes(2);
  });

  it("replays explicit cleared context without inventing a workbar", () => {
    const h = setup();
    h.view.renderContextUsage(null, true); h.view.updateWorkbar(null);
    h.postMessage.mockClear(); h.view.replayState();
    expect(h.postMessage.mock.calls).toEqual([[{ type: "chatAdmission", admission: null }], [{ type: "renderContextUsage", usage: null, stale: true }]]);
  });

  it("discards older model refreshes even when they complete last", async () => {
    const h = setup();
    let finish!: (value: ComposerModelState) => void;
    h.listModels.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const old = h.view.refreshModels(); await h.view.refreshModels();
    finish({ current: null, options: ["old"] }); await old;
    expect(h.postMessage).toHaveBeenCalledExactlyOnceWith({ type: "modelList", current: null, options: ["new"] });
  });

  it.each(["replace", "close"] as const)("discards a model result after view %s", async action => {
    const h = setup();
    let finish!: (value: ComposerModelState) => void;
    h.listModels.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const pending = h.view.refreshModels(); h[action]();
    finish({ current: null, options: ["stale"] }); await pending;
    expect(h.postMessage).not.toHaveBeenCalled();
  });
});


it("retains project admission status when a shared view is replayed", () => {
  const h = setup(); const admission = { threadId: "origin", state: "unknown" as const, checking: false };
  h.view.renderChatAdmission(admission); h.postMessage.mockClear(); h.view.replayState();
  expect(h.postMessage).toHaveBeenCalledWith({ type: "chatAdmission", admission });
  h.view.renderChatAdmission(null); h.postMessage.mockClear(); h.view.replayState();
  expect(h.postMessage).toHaveBeenCalledWith({ type: "chatAdmission", admission: null });
});

it("replays the retained history freshness status alongside pending origin admission", () => {
  const f = setup(); const history = { threadId: "a", phase: "unavailable" as const, cached: true };
  f.view.renderHistoryState(history); f.view.renderChatAdmission({ threadId: "other", state: "unknown", checking: false });
  f.postMessage.mockClear(); f.replace(); f.view.replayState();
  expect(f.postMessage).toHaveBeenCalledWith({ type: "historyState", history });
  expect(f.postMessage).toHaveBeenCalledWith({ type: "chatAdmission", admission: { threadId: "other", state: "unknown", checking: false } });
});


it("clears the retained workbar across thread selection while retaining admission", () => {
  const h = setup();
  h.view.clearThread("a"); h.view.updateWorkbar({ phaseLabel: "A planning" });
  const admission = { threadId: "a", state: "unknown" as const, checking: false };
  h.view.renderChatAdmission(admission); h.view.clearThread("b"); h.replace();
  h.postMessage.mockClear(); h.view.replayState();
  expect(h.postMessage.mock.calls.filter(([m]) => m.type === "updateWorkbar" && m.info !== null)).toEqual([]);
  expect(h.postMessage).toHaveBeenCalledWith({ type: "chatAdmission", admission });
});
