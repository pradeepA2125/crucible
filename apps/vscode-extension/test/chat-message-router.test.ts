import { describe, expect, it, vi } from "vitest";
import { routeChatMessage, type ChatMessageActions, type ChatMessageView, type LegacyTaskActions } from "../src/chat-message-router.js";

function setup() {
  const legacyTaskActions = {
    onPlanAction: vi.fn<LegacyTaskActions["onPlanAction"]>(async () => {}),
    onApplyInlineChange: vi.fn<LegacyTaskActions["onApplyInlineChange"]>(async () => {}),
    onDiscardInlineChange: vi.fn<LegacyTaskActions["onDiscardInlineChange"]>(async () => {}),
    onScopeDecision: vi.fn<LegacyTaskActions["onScopeDecision"]>(async () => {}),
    onValidationDecision: vi.fn<LegacyTaskActions["onValidationDecision"]>(async () => {}),
    onStepDecision: vi.fn<LegacyTaskActions["onStepDecision"]>(async () => {}),
    onAcceptTask: vi.fn<LegacyTaskActions["onAcceptTask"]>(async () => {}),
    onRejectTask: vi.fn<LegacyTaskActions["onRejectTask"]>(async () => {}),
    onResumeTask: vi.fn<LegacyTaskActions["onResumeTask"]>(async () => {}),
    onAbortTask: vi.fn<LegacyTaskActions["onAbortTask"]>(async () => {}),
  } satisfies LegacyTaskActions;

  const actions = {
    legacyTaskActions,
    onViewDiffFile: vi.fn<ChatMessageActions["onViewDiffFile"]>(async () => {}),
    onMessage: vi.fn<ChatMessageActions["onMessage"]>(async () => {}),
    onNewChat: vi.fn<ChatMessageActions["onNewChat"]>(async () => {}),
    onSwitchThread: vi.fn<ChatMessageActions["onSwitchThread"]>(async () => {}),
    onCommandDecision: vi.fn<ChatMessageActions["onCommandDecision"]>(async () => {}),
    onModeDecision: vi.fn<ChatMessageActions["onModeDecision"]>(async () => {}),
    onClarifyDecision: vi.fn<ChatMessageActions["onClarifyDecision"]>(async () => {}),
    onEditDecision: vi.fn<ChatMessageActions["onEditDecision"]>(async () => {}),
    onStopTurn: vi.fn<ChatMessageActions["onStopTurn"]>(() => {}),
    onSetReviewPref: vi.fn<ChatMessageActions["onSetReviewPref"]>(async () => {}),
    onListPrompts: vi.fn<ChatMessageActions["onListPrompts"]>(async () => ["review"]),
    onExpandPrompt: vi.fn<ChatMessageActions["onExpandPrompt"]>(async () => ({ found: true, text: "expanded" }) ),
    onListSkills: vi.fn<ChatMessageActions["onListSkills"]>(async () => [{ name: "audit", description: "Review changes" }]),
    onMcpDecision: vi.fn<ChatMessageActions["onMcpDecision"]>(async () => {}),
    onListModels: vi.fn<ChatMessageActions["onListModels"]>(async () => ({ current: null, options: [] }) ),
    onSetModel: vi.fn<ChatMessageActions["onSetModel"]>(async () => ({ current: null, options: [] }) ),
    onSetReasoningEffort: vi.fn<ChatMessageActions["onSetReasoningEffort"]>(async () => ({ current: null, options: [] }) ),
    onOpenSettings: vi.fn<ChatMessageActions["onOpenSettings"]>(() => {}),
    onOpenMemoryPanel: vi.fn<ChatMessageActions["onOpenMemoryPanel"]>(() => {}),
    onListWorkspaceFiles: vi.fn<ChatMessageActions["onListWorkspaceFiles"]>(async () => ["src/main.ts"]),
    onOpenFile: vi.fn<ChatMessageActions["onOpenFile"]>(() => {}),
    onOpenGraphPanel: vi.fn<ChatMessageActions["onOpenGraphPanel"]>(() => {}),
    onFetchSessionTranscript: vi.fn<ChatMessageActions["onFetchSessionTranscript"]>(async () => null ),
    onGetPlanMode: vi.fn<ChatMessageActions["onGetPlanMode"]>(() => true ),
    onSetPlanMode: vi.fn<ChatMessageActions["onSetPlanMode"]>(async () => {}),
    onGetStepReview: vi.fn<ChatMessageActions["onGetStepReview"]>(() => false ),
    onRewindPreview: vi.fn<ChatMessageActions["onRewindPreview"]>(async () => {}),
    onRewindConfirm: vi.fn<ChatMessageActions["onRewindConfirm"]>(async () => {}),
    onReady: vi.fn<ChatMessageActions["onReady"]>(async () => {}),
    onSetOpenAgents: vi.fn<ChatMessageActions["onSetOpenAgents"]>(() => {}),
    onStopAgent: vi.fn<ChatMessageActions["onStopAgent"]>(async () => {}),
    onStopAllAgents: vi.fn<ChatMessageActions["onStopAllAgents"]>(async () => {}),
    onSetOpenTeams: vi.fn<ChatMessageActions["onSetOpenTeams"]>(() => {}),
    onDisbandTeam: vi.fn<ChatMessageActions["onDisbandTeam"]>(async () => {}),
    onTeamPlanDecision: vi.fn<ChatMessageActions["onTeamPlanDecision"]>(async () => {}),
  } satisfies ChatMessageActions;
  const view = {
    postMessage: vi.fn<ChatMessageView["postMessage"]>(),
    replayState: vi.fn<ChatMessageView["replayState"]>(),
    answerFullAccessQuestion: vi.fn<ChatMessageView["answerFullAccessQuestion"]>(),
    setApprovalMode: vi.fn<ChatMessageView["setApprovalMode"]>(async () => {}),
    refreshModels: vi.fn<ChatMessageView["refreshModels"]>(async () => {}),
    showError: vi.fn<ChatMessageView["showError"]>(),
  } satisfies ChatMessageView;
  return { actions, legacyTaskActions, view, receive: (message: unknown) => routeChatMessage(message, actions, view) };
}

describe("shared chat host routing", () => {
  it.each([null, [], "sendMessage", 1, {}, { type: "unknown" }])("ignores non-chat messages: %j", async message => {
    const h = setup();
    await h.receive(message);
    for (const action of Object.values({ ...h.actions, legacyTaskActions: undefined, ...h.legacyTaskActions }).filter(value => typeof value === "function")) expect(action).not.toHaveBeenCalled();
    expect(h.view.postMessage).not.toHaveBeenCalled();
  });

  it("replays host state before hydration and publishes authoritative preferences", async () => {
    const h = setup();
    await h.receive({ type: "webviewReady" });
    expect(h.view.replayState).toHaveBeenCalledOnce();
    expect(h.view.replayState.mock.invocationCallOrder[0]).toBeLessThan(h.actions.onReady.mock.invocationCallOrder[0]!);
    expect(h.view.postMessage.mock.calls).toEqual([
      [{ type: "planModeState", enabled: true }],
      [{ type: "reviewPrefState", enabled: false }],
    ]);
  });

  it.each([undefined, false, true])("preserves sticky Plan Mode unless explicitly supplied: %s", async planMode => {
    const h = setup();
    const attachment = { name: "screen.png", mediaType: "image/png", dataUrl: "data:image/png;base64,AA==", bytes: 1 };
    await h.receive({ type: "sendMessage", text: "check", stepReview: true,
      forcedSkills: ["audit"], mentionedPaths: ["src/main.ts"],
      imageAttachments: [attachment, { name: 1 }], draftId: "draft-1", originalDraftText: "original", ...(planMode === undefined ? {} : { planMode }),
    });
    expect(h.actions.onMessage).toHaveBeenCalledExactlyOnceWith("check", true, ["audit"], ["src/main.ts"], planMode ?? true, [attachment], "draft-1", "original");
  });

  it.each(["exact", "prefix", "binary"])("preserves %s command rules and gate identity", async scope => {
    const h = setup();
    await h.receive({ type: "commandDecision", taskId: "thread-1", gateId: "gate-2", approve: true,
      remember: true, scope, ruleValue: "npm test" });
    expect(h.actions.onCommandDecision).toHaveBeenCalledExactlyOnceWith("thread-1",
      { approve: true, remember: true, scope, ruleValue: "npm test" }, "gate-2");
  });

  it("keeps edit and MCP decisions bound to their original thread/gate", async () => {
    const h = setup();
    await h.receive({ type: "editDecision", threadId: "one", gateId: "edit", decision: "reject", reason: "revise" });
    await h.receive({ type: "mcpDecision", threadId: "two", gateId: "mcp", approve: true, remember: false });
    expect(h.actions.onEditDecision).toHaveBeenCalledExactlyOnceWith("one", "reject", "revise", "edit");
    expect(h.actions.onMcpDecision).toHaveBeenCalledExactlyOnceWith("two", { approve: true, remember: false }, "mcp");
  });

  it("routes Full access confirmation separately from changing approval policy", async () => {
    const h = setup();
    await h.receive({ type: "fullAccessFirstSendChoice", requestId: "question", choice: "accept_edits" });
    expect(h.view.answerFullAccessQuestion).toHaveBeenCalledExactlyOnceWith("question", "accept_edits");
    expect(h.view.setApprovalMode).not.toHaveBeenCalled();
    await h.receive({ type: "setApprovalMode", mode: "full_access", confirmedFullAccess: true, dontShowAgain: true });
    expect(h.view.setApprovalMode).toHaveBeenCalledExactlyOnceWith("full_access", true, true);
  });

  it("does not echo Plan Mode before persistence completes", async () => {
    const h = setup();
    let persist!: () => void;
    h.actions.onSetPlanMode.mockImplementation(() => new Promise(resolve => { persist = resolve; }));
    const pending = h.receive({ type: "setPlanMode", enabled: false });
    expect(h.view.postMessage).not.toHaveBeenCalled();
    persist(); await pending;
    expect(h.view.postMessage).toHaveBeenCalledExactlyOnceWith({ type: "planModeState", enabled: false });
  });

  it("echoes review checkbox using the inverse of persisted auto-accept", async () => {
    const h = setup();
    await h.receive({ type: "setReviewPref", autoAccept: true });
    expect(h.actions.onSetReviewPref).toHaveBeenCalledExactlyOnceWith(true);
    expect(h.view.postMessage).toHaveBeenCalledExactlyOnceWith({ type: "reviewPrefState", enabled: false });
  });

  it.each([
    ["setModel", "onSetModel", "modelSwapError", { backend: "openai", model: "test" }],
    ["setReasoningEffort", "onSetReasoningEffort", "effortSwapError", { level: "high" }],
  ] as const)("keeps %s failures in the menu without unlocking a running composer", async (type, action, errorType, fields) => {
    const h = setup();
    h.actions[action].mockRejectedValueOnce(new Error("provider unavailable"));
    await h.receive({ type, ...fields });
    expect(h.view.postMessage).toHaveBeenCalledExactlyOnceWith({ type: errorType, message: "provider unavailable" });
    expect(h.view.showError).not.toHaveBeenCalled();
  });

  it("recovers input after an ordinary rejected action", async () => {
    const h = setup();
    h.actions.onMessage.mockRejectedValueOnce(new Error("send failed"));
    await h.receive({ type: "sendMessage", text: "check" });
    expect(h.view.postMessage).toHaveBeenCalledExactlyOnceWith({ type: "setInputEnabled", enabled: true });
    expect(h.view.showError).toHaveBeenCalledExactlyOnceWith("Chat error: send failed");
  });

  it("filters inspected agent/team IDs and rejects unknown team decisions", async () => {
    const h = setup();
    await h.receive({ type: "setOpenAgents", agentIds: ["a", null, 1] });
    await h.receive({ type: "setOpenTeams", teamIds: [false, "t"] });
    await h.receive({ type: "teamPlanDecision", threadId: "one", gateId: "g", decision: "yes" });
    expect(h.actions.onSetOpenAgents).toHaveBeenCalledExactlyOnceWith(["a"]);
    expect(h.actions.onSetOpenTeams).toHaveBeenCalledExactlyOnceWith(["t"]);
    expect(h.actions.onTeamPlanDecision).not.toHaveBeenCalled();
    await h.receive({ type: "teamPlanDecision", threadId: "one", gateId: "g", decision: "feedback", feedback: "revise" });
    expect(h.actions.onTeamPlanDecision).toHaveBeenCalledExactlyOnceWith("one", "g", "feedback", "revise");
  });

  it("retains session and prompt request correlation", async () => {
    const h = setup();
    await h.receive({ type: "fetchSessionTranscript", sessionId: "session" });
    await h.receive({ type: "expandPrompt", name: "review", args: "file", requestId: "request" });
    expect(h.view.postMessage.mock.calls).toEqual([
      [{ type: "sessionTranscript", sessionId: "session", transcript: null }],
      [{ type: "promptExpanded", requestId: "request", name: "review", found: true, text: "expanded" }],
    ]);
  });

  it("routes Stop synchronously without changing approval or preferences", () => {
    const h = setup();
    h.receive({ type: "stopTurn" });
    expect(h.actions.onStopTurn).toHaveBeenCalledOnce();
    expect(h.view.postMessage).not.toHaveBeenCalled();
  });
});

// The public protocol remains reachable through either host; these cases catch
// disconnected UI actions when the adapter or routing is extracted again.
describe("chat protocol action reachability", () => {
  const cases: { message: Record<string, unknown>; target: "actions" | "view" | "legacyTaskActions"; action: string; args: unknown[] }[] = [
    { message: { type: "webviewReady" }, target: "actions", action: "onReady", args: [] },
    { message: { type: "fullAccessFirstSendChoice", requestId: "q", choice: "cancel" }, target: "view", action: "answerFullAccessQuestion", args: ["q", "cancel"] },
    { message: { type: "setApprovalMode", mode: "ask" }, target: "view", action: "setApprovalMode", args: ["ask", false, false] },
    { message: { type: "setPlanMode", enabled: true }, target: "actions", action: "onSetPlanMode", args: [true] },
    { message: { type: "sendMessage", text: "hello", planMode: false }, target: "actions", action: "onMessage", args: ["hello", false, undefined, undefined, false, undefined, undefined, undefined] },
    { message: { type: "implementPlan", taskId: "t" }, target: "legacyTaskActions", action: "onPlanAction", args: ["t", "implement"] },
    { message: { type: "planFeedback", taskId: "t", feedback: "revise" }, target: "legacyTaskActions", action: "onPlanAction", args: ["t", "feedback", "revise"] },
    { message: { type: "newChat" }, target: "actions", action: "onNewChat", args: [] },
    { message: { type: "switchThread", threadId: "t" }, target: "actions", action: "onSwitchThread", args: ["t"] },
    { message: { type: "applyInlineChange", taskId: "t" }, target: "legacyTaskActions", action: "onApplyInlineChange", args: ["t"] },
    { message: { type: "discardInlineChange", taskId: "t" }, target: "legacyTaskActions", action: "onDiscardInlineChange", args: ["t"] },
    { message: { type: "viewDiffFile", path: "file" }, target: "actions", action: "onViewDiffFile", args: ["file", ""] },
    { message: { type: "scopeDecision", taskId: "t", files: ["file"], decision: "approve", remember: true }, target: "legacyTaskActions", action: "onScopeDecision", args: ["t", ["file"], "approve", true] },
    { message: { type: "validationDecision", taskId: "t", decision: "accept" }, target: "legacyTaskActions", action: "onValidationDecision", args: ["t", "accept"] },
    { message: { type: "commandDecision", taskId: "t", gateId: "g", approve: true }, target: "actions", action: "onCommandDecision", args: ["t", { approve: true, remember: false, scope: "exact", ruleValue: undefined }, "g"] },
    { message: { type: "mcpDecision", threadId: "t", gateId: "g", approve: true }, target: "actions", action: "onMcpDecision", args: ["t", { approve: true, remember: false }, "g"] },
    { message: { type: "stepDecision", taskId: "t", decision: "accept" }, target: "legacyTaskActions", action: "onStepDecision", args: ["t", "accept"] },
    { message: { type: "rewindPreview", messageId: "m" }, target: "actions", action: "onRewindPreview", args: ["m"] },
    { message: { type: "rewindConfirm", messageId: "m" }, target: "actions", action: "onRewindConfirm", args: ["m"] },
    { message: { type: "modeDecision", threadId: "t", mode: "implement" }, target: "actions", action: "onModeDecision", args: ["t", "implement"] },
    { message: { type: "clarifyDecision", threadId: "t", answer: "yes" }, target: "actions", action: "onClarifyDecision", args: ["t", "yes"] },
    { message: { type: "editDecision", threadId: "t", gateId: "g", decision: "accept" }, target: "actions", action: "onEditDecision", args: ["t", "accept", "", "g"] },
    { message: { type: "acceptTask", taskId: "t" }, target: "legacyTaskActions", action: "onAcceptTask", args: ["t"] },
    { message: { type: "rejectTask", taskId: "t", reason: "revise" }, target: "legacyTaskActions", action: "onRejectTask", args: ["t", "revise"] },
    { message: { type: "resumeTask", taskId: "t", stage: "plan" }, target: "legacyTaskActions", action: "onResumeTask", args: ["t", "plan"] },
    { message: { type: "setOpenAgents", agentIds: ["a"] }, target: "actions", action: "onSetOpenAgents", args: [["a"]] },
    { message: { type: "stopAgent", agentId: "a" }, target: "actions", action: "onStopAgent", args: ["a"] },
    { message: { type: "stopAllAgents" }, target: "actions", action: "onStopAllAgents", args: [] },
    { message: { type: "setOpenTeams", teamIds: ["team"] }, target: "actions", action: "onSetOpenTeams", args: [["team"]] },
    { message: { type: "disbandTeam", teamId: "team" }, target: "actions", action: "onDisbandTeam", args: ["team"] },
    { message: { type: "teamPlanDecision", threadId: "t", gateId: "g", decision: "approve" }, target: "actions", action: "onTeamPlanDecision", args: ["t", "g", "approve", undefined] },
    { message: { type: "stopTurn" }, target: "actions", action: "onStopTurn", args: [] },
    { message: { type: "abortTask", revert: true }, target: "legacyTaskActions", action: "onAbortTask", args: [true] },
    { message: { type: "setReviewPref", autoAccept: true }, target: "actions", action: "onSetReviewPref", args: [true] },
    { message: { type: "listPrompts" }, target: "actions", action: "onListPrompts", args: [] },
    { message: { type: "listSkills" }, target: "actions", action: "onListSkills", args: [] },
    { message: { type: "listWorkspaceFiles" }, target: "actions", action: "onListWorkspaceFiles", args: [] },
    { message: { type: "fetchSessionTranscript", sessionId: "s" }, target: "actions", action: "onFetchSessionTranscript", args: ["s"] },
    { message: { type: "openFile", path: "file" }, target: "actions", action: "onOpenFile", args: ["file"] },
    { message: { type: "expandPrompt", name: "review", args: "file" }, target: "actions", action: "onExpandPrompt", args: ["review", "file"] },
    { message: { type: "listModels" }, target: "view", action: "refreshModels", args: [] },
    { message: { type: "setModel", backend: "openai", model: "model" }, target: "actions", action: "onSetModel", args: ["openai", "model"] },
    { message: { type: "setReasoningEffort", level: null }, target: "actions", action: "onSetReasoningEffort", args: [null] },
    { message: { type: "openSettings", section: "providers" }, target: "actions", action: "onOpenSettings", args: ["providers"] },
    { message: { type: "openMemoryPanel" }, target: "actions", action: "onOpenMemoryPanel", args: [] },
    { message: { type: "openGraphPanel" }, target: "actions", action: "onOpenGraphPanel", args: [] },
  ];
  it.each(cases)("$message.type reaches $action once", async ({ message, target, action, args }) => {
    const h = setup();
    await h.receive(message);
    const calls = Object.entries(h[target]);
    const selected = calls.find(([name]) => name === action)?.[1];
    expect(selected).toHaveBeenCalledExactlyOnceWith(...args);
    for (const [name, callback] of Object.entries({ ...h.actions, legacyTaskActions: undefined, ...h.legacyTaskActions })) {
      if (typeof callback !== "function") continue;
      if ((target === "actions" || target === "legacyTaskActions") && name === action) continue;
      if (name === "onGetPlanMode" || name === "onGetStepReview") continue;
      expect(callback, `unexpected action ${name}`).not.toHaveBeenCalled();
    }
  });
});


describe("chat host without the optional legacy task subsystem", () => {
  it.each(["implementPlan", "planFeedback", "applyInlineChange", "discardInlineChange",
    "scopeDecision", "validationDecision", "stepDecision", "acceptTask", "rejectTask", "resumeTask", "abortTask"])("ignores %s without changing composer state", async type => {
    const h = setup();
    const { legacyTaskActions: _legacy, ...chat } = h.actions;
    await routeChatMessage({ type }, chat, h.view);
    for (const action of Object.values(chat)) expect(action).not.toHaveBeenCalled();
    for (const action of Object.values(h.legacyTaskActions)) expect(action).not.toHaveBeenCalled();
    for (const action of Object.values(h.view)) expect(action).not.toHaveBeenCalled();
  });
  it("keeps reactive edit diffs available without the task port", async () => {
    const h = setup(); const { legacyTaskActions: _legacy, ...chat } = h.actions;
    await routeChatMessage({ type: "viewDiffFile", path: "hello.py" }, chat, h.view);
    expect(chat.onViewDiffFile).toHaveBeenCalledExactlyOnceWith("hello.py", "");
  });
  it("keeps current command approvals available without the task port", async () => {
    const h = setup(); const { legacyTaskActions: _legacy, ...chat } = h.actions;
    await routeChatMessage({ type: "commandDecision", taskId: "thread", gateId: "gate", approve: true }, chat, h.view);
    expect(chat.onCommandDecision).toHaveBeenCalledExactlyOnceWith("thread", { approve: true, remember: false, scope: "exact", ruleValue: undefined }, "gate");
  });
});


it("routes Check receipt as a read-only action without sending or restoring a draft", async () => {
  const h = setup(); const check = vi.fn(async () => {});
  await routeChatMessage({ type: "checkChatReceipt" }, { ...h.actions, onCheckChatReceipt: check }, h.view);
  expect(check).toHaveBeenCalledOnce(); expect(h.actions.onMessage).not.toHaveBeenCalled(); expect(h.view.postMessage).not.toHaveBeenCalled();
});


it("validates exact gate intents without calling legacy decisions or composer recovery", async () => {
  const h = setup(), exact = vi.fn(async () => {});
  const actions: ChatMessageActions = { ...h.actions, onGateDecision: exact };
  const action = { type: "gateDecision", threadId: "a", gateId: "g", kind: "feedback", value: "refine" };
  await routeChatMessage({ ...action, arbitrary: true }, actions, h.view); expect(exact).not.toHaveBeenCalled();
  await routeChatMessage(action, actions, h.view); expect(exact).toHaveBeenCalledExactlyOnceWith(action);
  expect(h.actions.onMessage).not.toHaveBeenCalled(); expect(h.actions.onModeDecision).not.toHaveBeenCalled();
  exact.mockRejectedValueOnce(new Error("gate refused")); await routeChatMessage(action, actions, h.view);
  expect(h.view.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "decisionRetry", threadId: "a", gateId: "g", kind: "mode" }));
  expect(h.view.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "setInputEnabled" }));
});


it("returns exact correlated oversized/malformed refusals without losing the composer", async () => {
  const h = setup(), exact = vi.fn(async () => {}), actions = { ...h.actions, onGateDecision: exact };
  await routeChatMessage({ type: "gateDecision", threadId: "a", gateId: "g", kind: "feedback", value: "x".repeat(65537) }, actions, h.view);
  expect(exact).not.toHaveBeenCalled();
  expect(h.view.postMessage).toHaveBeenCalledWith({ type: "decisionRetry", threadId: "a", gateId: "g", kind: "mode" });
  expect(h.view.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "gateDecisionNotice", gateId: "g" }));
  expect(h.view.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "setInputEnabled" }));
});
