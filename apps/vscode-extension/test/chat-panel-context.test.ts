import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { describe, expect, test, vi } from "vitest";
import { createControllerViewUI } from "../src/controller-view-ui.js";
import { ChatView } from "../src/chat-view.js";
import { DraftSession } from "../src/drafts/draft-session.js";
import { routeChatMessage } from "../src/chat-message-router.js";
import { ThemeHub, themeReadyReply } from "../src/theme-settings.js";
const text = readFileSync(new URL("../src/chat-panel.ts", import.meta.url), "utf8");
const tree = ts.createSourceFile("chat-panel.ts", text, ts.ScriptTarget.Latest, true);
const declaration = tree.statements.find(n => ts.isClassDeclaration(n) && n.name?.text === "ChatPanel")!;
const js = ts.transpile(declaration.getText(tree), { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS });
// The class source runs without its module scope, so bind the theme helpers it uses
// (theming Phase 1: the message handler answers themeReady before anything else).
const themeHub = new ThemeHub();
const panelNotices = { window: { showErrorMessage: vi.fn() } };
const ChatPanel = new Function("exports", "themeReadyReply", "themeHub", "randomUUID", "routeChatMessage", "ChatView", "DraftSession", "vscode", `${js}; return ChatPanel;`)({}, themeReadyReply, themeHub, randomUUID, routeChatMessage, ChatView, DraftSession, panelNotices);
function setup() {
  const host = Object.create(ChatPanel.prototype) as any;
  let receive!: (message: unknown) => void;
  const postMessage = vi.fn();
  host.panel = { webview: { postMessage, onDidReceiveMessage: (fn: typeof receive) => { receive = fn; } }, onDidDispose: () => {} };
  host.onGetPlanMode = () => false; host.onGetStepReview = () => true; host.onReady = async () => {}; host.lastWorkbarInfo = null; host.lastContextUsage = null; host.contextUsageStale = false; host.modelRefreshEpoch = 0;
  host.fullAccessQuestions = new Map();
  host.draftGeneration = {};
  host.drafts = new DraftSession({ post: (message: Record<string, unknown>) => host.panel?.webview.postMessage(message), identity: () => host.panel ? host.draftGeneration : null, admission: (value: unknown) => host.renderChatAdmission(value) });
  // This fixture bypasses the constructors, so mirror retained defaults explicitly.
  host.chatAdmission = null; host.receiptCheckHandler = async () => {};
  host.viewPort = {
    postMessage: (message: Record<string, unknown>) => { host.drafts.observe(message); host.panel?.webview.postMessage(message); },
    isOpen: () => host.panel !== null, identity: () => host.panel,
    createRequestId: randomUUID, listModels: () => host.onListModels(),
  };
  return { host, postMessage, receive: (m: unknown) => receive(m) };
}
describe("ChatPanel first-send Full access question", () => {
  test("asks the React app, re-asks after a webview reload and resolves with its answer", async () => {
    const { host, postMessage, receive } = setup();
    host.registerHandlers();
    const answer = host.confirmFullAccessFirstSend("repo");
    const [[asked]] = postMessage.mock.calls;
    expect(asked).toEqual({ type: "confirmFullAccessFirstSend", requestId: expect.any(String), workspace: "repo" });
    postMessage.mockClear();
    receive({ type: "webviewReady" });
    expect(postMessage).toHaveBeenCalledWith(asked);
    receive({ type: "fullAccessFirstSendChoice", requestId: asked.requestId, choice: "accept_edits" });
    await expect(answer).resolves.toBe("accept_edits");
  });
  test("an unknown answer is Cancel, and no panel means Cancel", async () => {
    const { host, postMessage, receive } = setup();
    host.registerHandlers();
    const answer = host.confirmFullAccessFirstSend("repo");
    receive({ type: "fullAccessFirstSendChoice", requestId: postMessage.mock.calls[0][0].requestId, choice: "yes please" });
    await expect(answer).resolves.toBe("cancel");
    host.panel = null;
    await expect(host.confirmFullAccessFirstSend("repo")).resolves.toBe("cancel");
  });
});

describe("ChatPanel context bridge", () => {
  test("thread hydration forwards the selected identity while routine clears remain compatible", () => {
    const { host, postMessage } = setup();
    host.clearThread("second-thread");
    host.clearThread();
    expect(postMessage.mock.calls).toEqual([
      [{ type: "clearThread", threadId: "second-thread" }],
      [{ type: "clearThread" }],
    ]);
  });
  test("the production controller UI adapter forwards measurements and clears to the panel", () => {
    const source = readFileSync(new URL("../src/extension.ts", import.meta.url), "utf8");
    const ast = ts.createSourceFile("extension.ts", source, ts.ScriptTarget.Latest, true);
    let adapter: ts.Expression | undefined;
    function visit(node: ts.Node): void {
      if (ts.isVariableDeclaration(node) && node.name.getText(ast) === "ui") adapter = node.initializer;
      ts.forEachChild(node, visit);
    }
    visit(ast);
    if (!adapter || !ts.isObjectLiteralExpression(adapter)) throw new Error("Production UI adapter missing");
    expect(adapter.properties.some(n => ts.isSpreadAssignment(n) && n.expression.getText(ast) === "createControllerViewUI(chatPanel)")).toBe(true);
    const { host, postMessage } = setup();
    const forward = createControllerViewUI(host).renderContextUsage;
    const observation = { input_tokens: 52063, context_window: 272000 };
    forward(observation, false);
    forward(null, true);
    expect(postMessage.mock.calls).toEqual([
      [{ type: "renderContextUsage", usage: observation, stale: false }],
      [{ type: "renderContextUsage", usage: null, stale: true }],
    ]);
  });
  test("replays retained observation and stale flag on every webviewReady", () => {
    const { host, postMessage, receive } = setup();
    const observation = { input_tokens: 0, observed_at: "2026-10-08T00:00:00Z" };
    host.renderContextUsage(observation, true); host.registerHandlers(); postMessage.mockClear();
    receive({ type: "webviewReady" }); receive({ type: "webviewReady" });
    expect(postMessage.mock.calls.filter(([m]) => m.type === "renderContextUsage")).toEqual([[{ type: "renderContextUsage", usage: observation, stale: true }], [{ type: "renderContextUsage", usage: observation, stale: true }]]);
  });
  test("older model refresh cannot replace a newer menu or a replacement panel", async () => {
    const { host, postMessage } = setup(); let finish!: (result: unknown) => void; let first = true;
    host.onListModels = async () => { if (first) { first = false; return new Promise(r => { finish = r; }); } return { current: null, options: ["new"] }; };
    const old = host.refreshModels(); await host.refreshModels(); finish({ options: ["old"] }); await old;
    expect(postMessage).toHaveBeenCalledTimes(1); expect(postMessage.mock.calls[0][0].options).toEqual(["new"]);
    host.onListModels = () => new Promise(r => { finish = r; }); const pending = host.refreshModels();
    host.panel = { webview: { postMessage: vi.fn() } }; finish({ options: ["wrong-panel"] }); await pending;
    expect(postMessage).toHaveBeenCalledTimes(1); expect(host.panel.webview.postMessage).not.toHaveBeenCalled();
  });
  test("answers the webview's themeReady with the current theme before any other handling", () => {
    const { host, postMessage, receive } = setup();
    host.registerHandlers();
    themeHub.update({ appearance: "dark", accent: "violet" });
    postMessage.mockClear();
    receive({ type: "themeReady" });
    expect(postMessage).toHaveBeenCalledExactlyOnceWith({ type: "themeChanged", appearance: "dark", accent: "violet" });
  });
});


describe("ChatPanel async reply lifetime", () => {
  test.each(["replace", "dispose"])("does not reply to an old view after %s", async action => {
    const { host, postMessage, receive } = setup();
    let finish!: (value: string[]) => void;
    host.onListPrompts = () => new Promise<string[]>(resolve => { finish = resolve; });
    host.registerHandlers();
    receive({ type: "listPrompts" });
    const replacementPost = vi.fn();
    host.panel = action === "replace" ? { webview: { postMessage: replacementPost } } : null;
    finish(["review"]);
    await Promise.resolve(); await Promise.resolve();
    expect(postMessage).not.toHaveBeenCalled();
    if (action === "replace") expect(replacementPost).toHaveBeenCalledExactlyOnceWith({ type: "promptList", names: ["review"] });
    else expect(replacementPost).not.toHaveBeenCalled();
  });
});


describe("ChatPanel shared view construction and handshake", () => {
  test("constructs without eagerly reading a view before the adapter is initialized", async () => {
    const host = new ChatPanel({});
    await expect(host.confirmFullAccessFirstSend("repo")).resolves.toBe("cancel");
  });
  test("keeps the complete ready replay before preferences and hydration", async () => {
    const { host, postMessage, receive } = setup();
    host.registerHandlers();
    host.renderApproval({ tier: "unknown", snapshot: null, mode: null, confirmed: false,
      pending: false, warningDismissed: false, trusted: true, error: null, notice: null });
    const answer = host.confirmFullAccessFirstSend("repo");
    const requestId = postMessage.mock.calls.find(([message]) => message.type === "confirmFullAccessFirstSend")![0].requestId;
    host.updateWorkbar({ phaseLabel: "Exploring" });
    host.onReady = async () => { postMessage({ type: "hydrationStarted" }); };
    postMessage.mockClear(); receive({ type: "webviewReady" });
    expect(postMessage.mock.calls.map(([message]) => message.type)).toEqual([
      "approvalState", "confirmFullAccessFirstSend", "chatAdmission", "renderContextUsage", "updateWorkbar",
      "planModeState", "reviewPrefState", "hydrationStarted",
    ]);
    expect(postMessage.mock.calls[2][0]).toEqual({ type: "chatAdmission", admission: null });
    receive({ type: "fullAccessFirstSendChoice", requestId, choice: "cancel" });
    await expect(answer).resolves.toBe("cancel");
  });
});


describe("ChatPanel real constructor port lifetime", () => {
  test("projects to the current panel and rejects an old panel's model result", async () => {
    const constructor = declaration.members.find(ts.isConstructorDeclaration)!;
    const modelIndex = constructor.parameters.findIndex(parameter => parameter.name.getText(tree) === "onListModels");
    expect(modelIndex).toBeGreaterThan(0);
    let finish!: (value: unknown) => void;
    const args: unknown[] = Array.from({ length: modelIndex + 1 }, () => undefined);
    args[0] = {};
    args[modelIndex] = () => new Promise(resolve => { finish = resolve; });
    const host = new ChatPanel(...args);
    const oldPost = vi.fn(); const currentPost = vi.fn();
    host.panel = { webview: { postMessage: oldPost } };
    host.appendChunk("old");
    const pending = host.refreshModels();
    host.panel = { webview: { postMessage: currentPost } };
    host.appendChunk("current");
    finish({ current: null, options: ["stale"] }); await pending;
    expect(oldPost).toHaveBeenCalledExactlyOnceWith({ type: "appendChunk", chunk: "old" });
    expect(currentPost).toHaveBeenCalledExactlyOnceWith({ type: "appendChunk", chunk: "current" });
    host.onListModels = async () => ({ current: null, options: ["fresh"] });
    await host.refreshModels();
    expect(currentPost).toHaveBeenLastCalledWith({ type: "modelList", current: null, options: ["fresh"] });
  });
});


describe("ChatPanel shared draft integration", () => {
  test("flushes the active composer before attention or history selection and guards a prepared first send", async () => {
    const { host, postMessage, receive } = setup(); host.registerHandlers(); host.clearThread("a");
    receive({ type: "draft/read", requestId: "read", threadId: "a" });
    host.onSwitchThread = vi.fn(async () => {});
    const attention = host.switchThread("b");
    const requestId = postMessage.mock.calls.find(([message]) => message.type === "draft/flush")![0].requestId;
    expect(host.onSwitchThread).not.toHaveBeenCalled();
    receive({ type: "draft/save", requestId: "save", threadId: "a", revision: 1, version: 0,
      draft: { text: "last edit", imageAttachments: [], mentionedPaths: ["a.ts"] } });
    receive({ type: "draft/flushAck", requestId }); await attention;
    expect(host.onSwitchThread).toHaveBeenCalledExactlyOnceWith("b");
    receive({ type: "draft/begin", requestId: "begin", threadId: "a", revision: 2, draftId: "draft",
      draft: { text: "first-send question", imageAttachments: [], mentionedPaths: [] } });
    await expect(host.switchThread("c")).rejects.toThrow("pending");
    expect(postMessage).toHaveBeenCalledWith({ type: "chatAdmission", admission: { threadId: "a", state: "pending", checking: false } });
    host.restoreDraft("first-send question", { draftId: "draft", imageAttachments: [] });
    expect(postMessage.mock.calls.some(([message]) => message.type === "draft/changed" && message.snapshot.returned?.draft.text === "first-send question")).toBe(true);
  });
  test("a reload cannot acknowledge the previous renderer's outstanding navigation flush", async () => {
    vi.useFakeTimers();
    try {
      const { host, postMessage, receive } = setup(); host.registerHandlers();
      receive({ type: "draft/read", requestId: "read", threadId: "new" });
      host.onSwitchThread = vi.fn(async () => {});
      const navigation = expect(host.switchThread("b")).rejects.toThrow("Navigation was cancelled");
      const requestId = postMessage.mock.calls.find(([message]) => message.type === "draft/flush")![0].requestId;
      receive({ type: "webviewReady" }); receive({ type: "draft/flushAck", requestId });
      await vi.advanceTimersByTimeAsync(2001); await navigation;
      expect(host.onSwitchThread).not.toHaveBeenCalled();
    } finally { vi.useRealTimers(); }
  });
});


test("a late identified send after reload is refused without consuming its returned draft; a new preparation still dispatches", async () => {
  const { host, receive, postMessage } = setup(); host.registerHandlers(); host.clearThread("a"); host.onMessage = vi.fn(async () => {});
  receive({ type: "draft/read", requestId: "read", threadId: "a" });
  receive({ type: "draft/begin", requestId: "begin", threadId: "a", revision: 1, draftId: "old",
    draft: { text: "original", imageAttachments: [], mentionedPaths: [] } });
  receive({ type: "webviewReady" });
  receive({ type: "sendMessage", draftId: "old", text: "late original" });
  await Promise.resolve(); expect(host.onMessage).not.toHaveBeenCalled();
  receive({ type: "draft/read", requestId: "verify", threadId: "a" });
  expect(postMessage.mock.calls.find(([message]) => message.requestId === "verify")![0].snapshot.returned.draft.text).toBe("original");
  receive({ type: "sendMessage", draftId: "old", text: "duplicate after read" });
  expect(host.onMessage).not.toHaveBeenCalled();
  host.clearThread("b"); receive({ type: "draft/read", requestId: "read-b", threadId: "b" });
  receive({ type: "draft/begin", requestId: "begin-b", threadId: "b", revision: 1, draftId: "new",
    draft: { text: "current", imageAttachments: [], mentionedPaths: [] } });
  receive({ type: "sendMessage", draftId: "new", text: "current" }); await Promise.resolve();
  expect(host.onMessage).toHaveBeenCalledTimes(1); expect(host.onMessage.mock.calls[0][6]).toBe("new");
});
