import type {
  AgentSummary,
  BackendTaskClient,
  ChatMessage,
  ChatThreadSummary,
  CommandDecision,
  Diagnostic,
  TaskResult,
  SendChatResult,
  StreamEvent,
  TaskSubmission,
  ThreadLiveState,
} from "@crucible/editor-client";
import { promises as fsp } from "fs";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { describe, expect, test, vi } from "vitest";

import {
  CrucibleController,
  type ControllerUI,
  type LiveGateView,
  type LivePlanView,
  type LiveSessionsView,
  type SettingsProvider,
} from "../src/controller.js";
import type { SessionStore } from "../src/session-store.js";
import type { ReviewFileEntry, TaskSessionState } from "../src/types.js";

class MemorySessionStore implements SessionStore {
  value: TaskSessionState | null = null;

  async load(): Promise<TaskSessionState | null> {
    return this.value;
  }

  async save(session: TaskSessionState): Promise<void> {
    this.value = session;
  }

  async clear(): Promise<void> {
    this.value = null;
  }
}

/** Wraps a generator stub as sendChatMessage's {kind: "stream"} result (spec §5.3). */
function asStream<A extends unknown[]>(
  gen: (...args: A) => AsyncIterable<StreamEvent>,
): (...args: A) => Promise<SendChatResult> {
  return async (...args: A) => ({ kind: "stream", events: gen(...args) });
}

interface StubBackendState {
  submitPayloads: TaskSubmission[];
  getTaskCalls: string[];
  acceptCalls: string[];
  rejectCalls: Array<{ taskId: string; reason: string }>;
  getResultCalls: string[];
  planFeedbackCalls: Array<{ taskId: string; feedback: string | null }>;
  liveResponse?: ThreadLiveState;
  liveCalls?: string[];
  abortCalls?: Array<{ taskId: string; revert: boolean }>;
  reviewPrefCalls?: Array<{ taskId: string; autoAccept: boolean }>;
  globalReviewPrefCalls?: Array<{ autoAccept: boolean }>;
  planModeCalls?: boolean[];
}

const NULL_LIVE_STATE: ThreadLiveState = {
  activeTaskId: null,
  status: null,
  pendingGates: [],
  plan: null,
  turnActive: false,
};

function createStubBackend(state: StubBackendState): BackendTaskClient {
  return {
    submitTask: async (input) => {
      state.submitPayloads.push(input);
      return { taskId: "task-123" };
    },
    getTask: async (taskId) => {
      state.getTaskCalls.push(taskId);
      return {
        taskId,
        goal: "goal",
        status: "READY_FOR_REVIEW",
        modifiedFiles: ["src/main.py"],
        diagnostics: [] as Diagnostic[],
      };
    },
    getTaskResult: async (taskId) => {
      state.getResultCalls.push(taskId);
      return {
        taskId,
        status: "READY_FOR_REVIEW",
        plan: {
          analysis: "a",
          steps: [
            {
              id: "1",
              goal: "g",
              targets: [{ path: "src/main.py", intent: "existing" }],
              risk: "low"
            }
          ],
          expected_files: ["src/main.py"],
          stop_conditions: ["done"],
        },
        patch: {
          patch_ops: [
            {
              op: "create_file",
              file: "src/main.py",
              content: "print('x')\n",
              reason: "seed",
            },
          ],
        },
        modifiedFiles: ["src/main.py"],
        diagnostics: [] as Diagnostic[],
        shadowWorkspacePath: "/tmp/shadow",
      } as TaskResult;
    },
    cancelTask: async (taskId) => ({ taskId, status: "ABORTED" }),
    abortTask: async (taskId, options) => {
      state.abortCalls?.push({ taskId, revert: options.revert });
      return { taskId, goal: "goal", status: "ABORTED", modifiedFiles: [], diagnostics: [] as Diagnostic[] };
    },
    setReviewPref: async (taskId, options) => {
      state.reviewPrefCalls?.push({ taskId, autoAccept: options.autoAccept });
      return { taskId, goal: "goal", status: "EXECUTING", modifiedFiles: [], diagnostics: [] as Diagnostic[] };
    },
    setGlobalReviewPref: async (options) => {
      state.globalReviewPrefCalls?.push({ autoAccept: options.autoAccept });
      return { autoResolved: 0, background: 0 };
    },
    setPlanMode: async (planMode) => {
      state.planModeCalls?.push(planMode);
    },
    acceptPatch: async (taskId) => {
      state.acceptCalls.push(taskId);
      return {
        taskId,
        status: "SUCCEEDED",
        modifiedFiles: ["src/main.py"],
        diagnostics: [] as Diagnostic[],
        shadowWorkspacePath: null,
      } as TaskResult;
    },
    rejectPatch: async (taskId, reason) => {
      state.rejectCalls.push({ taskId, reason });
      return {
        taskId,
        status: "ABORTED",
        modifiedFiles: ["src/main.py"],
        diagnostics: [] as Diagnostic[],
        shadowWorkspacePath: null,
      } as TaskResult;
    },
    providePlanFeedback: async (taskId, feedback) => {
      state.planFeedbackCalls.push({ taskId, feedback });
      return {
        taskId,
        goal: "goal",
        status: "AWAITING_PLAN_APPROVAL",
        modifiedFiles: [],
        diagnostics: [] as Diagnostic[],
        planMarkdown: feedback ? "# Revised Plan" : "# Approved Plan",
      };
    },
    resumeTask: async (_taskId) => ({ taskId: "task-child", resumeOfTaskId: _taskId }),
    sendScopeDecision: async (taskId, _decision) => ({ taskId, status: "EXECUTING" }),
    sendValidationDecision: async (taskId, _decision) => ({ taskId, status: "AWAITING_VALIDATION_DECISION" as const }),
    sendCommandDecision: async (taskId, _decision) => ({ taskId, status: "EXECUTING" as const }),
    streamPatch: async (_taskId, _onEvent, _signal) => {},
    streamPatchEvents: async function* (_taskId: string) {
      yield { type: "done" as const, payload: {} as Record<string, never> };
    },
    listChatThreads: async () => [],
    createChatThread: async (workspacePath: string, title?: string) => ({
      threadId: "chat-stub",
      workspacePath,
      title: title ?? "New Chat",
      createdAt: "2026-01-01T00:00:00Z",
    }),
    getChatThread: async (threadId: string) => ({
      threadId,
      workspacePath: "/tmp/workspace",
      title: "New Chat",
      messages: [],
      touchedFiles: [],
    }),
    getThreadLiveState: async (threadId: string) => {
      state.liveCalls?.push(threadId);
      return state.liveResponse ?? NULL_LIVE_STATE;
    },
    sendChatMessage: asStream(async function* (_threadId: string, _message: string, _signal?: AbortSignal) {
      yield { type: "chat_done" as const, payload: {} as Record<string, never> };
    }),
    applyInlineChange: async (_inlineTaskId: string) => {},
    discardInlineChange: async (_inlineTaskId: string) => {},
    sendStepDecision: async (_taskId: string, _decision: "accept" | "discard") => {},
    postModeDecision: async function* (_threadId: string, _mode: string) {
      yield { type: "chat_done" as const, payload: {} as Record<string, never> };
    },
    postEditDecision: async (
      _threadId: string,
      _decision: "accept" | "reject",
      _reason?: string,
      _gateId?: string,
    ) => {},
    postChatCommandDecision: async (
      _threadId: string,
      _decision: CommandDecision,
      _gateId?: string,
    ) => {},
    stopChatTurn: async (_threadId: string) => ({ ok: true }),
    streamChannel: async function* (_channelId: string) {
      yield { type: "chat_done" as const, payload: {} as Record<string, never> };
    },
  };
}

function createUi(overrides?: Partial<ControllerUI>): ControllerUI {
  return {
    getWorkspacePath: () => "/tmp/workspace",
    replaceChatMessages: () => {},
    removeChatMessage: () => {},
    restoreDraft: () => {},
    markQueued: () => {},
    promptForGoal: async () => "Ship the feature",
    promptForTaskId: async () => undefined,
    promptForRejectReason: async () => "Needs changes",
    promptForResumeStage: async () => undefined,
    promptForMaxIterationsOverride: async () => undefined,
    promptForScopeDecision: async () => undefined,
    showRewindPreview: () => {},
    prefillComposer: () => {},
    showInfo: () => {},
    showWarning: () => {},
    showError: () => {},
    openChatPanel: () => {},
    appendChatMessage: (_msg: ChatMessage) => {},
    appendChatChunk: (_chunk: string) => {},
    showChatThinking: (_message: string) => {},
    updateChatThinking: (_message: string) => {},
    hideChatThinking: () => {},
    setChatInputEnabled: (_enabled: boolean) => {},
    renderChatThreadList: (_threads: ChatThreadSummary[], _activeThreadId: string) => {},
    clearChatThread: () => {},
    resolveInlineChangeCard: (_taskId: string, _resolution: "applied" | "discarded") => {},
    updateThreadTitle: (_threadId: string, _title: string) => {},
    appendChatThinkingEntry: (_text: string) => {},
    appendChatThinkingChunk: (_chunk: string) => {},
    finalizeAgentMessage: () => {},
    renderLiveGates: () => {},
    clearLiveGates: () => {},
    renderLivePlan: () => {},
    clearLivePlan: () => {},
    appendToolEvent: () => {},
    appendToolResult: () => {},
    updateWorkbar: () => {},
    updateRetryStatus: () => {},
    updateTokenProgress: () => {},
    updateEditFailure: () => {},
    renderLiveReview: () => {},
    clearLiveReview: () => {},
    renderLiveError: () => {},
    clearLiveError: () => {},
    renderLiveTodos: () => {},
    clearLiveTodos: () => {},
    renderLiveSessions: () => {},
    clearLiveSessions: () => {},
    sendLiveStatus: () => {},
    promptSetup: () => {},
    renderAgents: () => {},
    agentDetail: () => {},
    agentEvent: () => {},
    renderTeams: () => {},
    renderLiveTeams: () => {},
    teamDetail: () => {},
    teamEvent: () => {},
    ...overrides,
  };
}

function createSettings(): SettingsProvider {
  return {
    getBackendBaseUrl: () => "http://127.0.0.1:8000",
    getDefaultMode: () => "project_edit",
    getPollIntervalMs: () => 10_000,
  };
}

describe("CrucibleController", () => {
  test("startTask submits expected workspace path and mode", async () => {
    const state: StubBackendState = {
      submitPayloads: [],
      getTaskCalls: [],
      acceptCalls: [],
      rejectCalls: [],
      getResultCalls: [],
      planFeedbackCalls: [],
    };
    const backend = createStubBackend(state);
    const store = new MemorySessionStore();

    const controller = new CrucibleController(
      () => backend,
      store,
      createSettings(),
      createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-03-03T00:00:00.000Z"
    );

    await controller.startTask();
    controller.dispose();

    expect(state.submitPayloads).toEqual([
      {
        goal: "Ship the feature",
        workspacePath: "/tmp/workspace",
        mode: "project_edit",
      },
    ]);
    expect(store.value?.taskId).toBe("task-123");
  });

  test("accept/reject call backend endpoints and refresh task state", async () => {
    const state: StubBackendState = {
      submitPayloads: [],
      getTaskCalls: [],
      acceptCalls: [],
      rejectCalls: [],
      getResultCalls: [],
      planFeedbackCalls: [],
    };
    const backend = createStubBackend(state);
    const store = new MemorySessionStore();
    store.value = {
      taskId: "task-xyz",
      status: "READY_FOR_REVIEW",
      workspacePath: "/tmp/workspace",
      backendBaseUrl: "http://127.0.0.1:8000",
      updatedAt: "2026-03-03T00:00:00.000Z",
    };

    const controller = new CrucibleController(
      () => backend,
      store,
      createSettings(),
      createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-03-03T00:00:00.000Z"
    );

    await controller.initialize();
    await controller.acceptPatch();
    await controller.rejectPatch();
    controller.dispose();

    expect(state.acceptCalls).toEqual(["task-xyz"]);
    expect(state.rejectCalls).toEqual([{ taskId: "task-xyz", reason: "Needs changes" }]);
    expect(state.getTaskCalls.length).toBeGreaterThanOrEqual(3);
  });

  test("accept handles 409 conflict by refreshing task state", async () => {
    const state: StubBackendState = {
      submitPayloads: [],
      getTaskCalls: [],
      acceptCalls: [],
      rejectCalls: [],
      getResultCalls: [],
      planFeedbackCalls: [],
    };

    const warnings: string[] = [];
    const store = new MemorySessionStore();
    store.value = {
      taskId: "task-409",
      status: "READY_FOR_REVIEW",
      workspacePath: "/tmp/workspace",
      backendBaseUrl: "http://127.0.0.1:8000",
      updatedAt: "2026-03-03T00:00:00.000Z",
    };

    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      acceptPatch: async () => {
        throw new Error("Backend request failed (409 Conflict) for /v1/tasks/task-409/accept");
      },
    };

    const controller = new CrucibleController(
      () => backend,
      store,
      createSettings(),
      createUi({
        showWarning: (message) => warnings.push(message),
      }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-03-03T00:00:00.000Z"
    );

    await controller.initialize();
    await controller.acceptPatch();
    controller.dispose();

    expect(warnings.some((message) => message.includes("Refreshing state"))).toBe(true);
    expect(state.getTaskCalls.length).toBeGreaterThanOrEqual(2);
  });

  test("providePlanFeedback sends null on approval and text on regeneration", async () => {
    const state: StubBackendState = {
      submitPayloads: [],
      getTaskCalls: [],
      acceptCalls: [],
      rejectCalls: [],
      getResultCalls: [],
      planFeedbackCalls: [],
    };
    const infos: string[] = [];
    const backend = createStubBackend(state);
    const store = new MemorySessionStore();
    store.value = {
      taskId: "task-plan",
      status: "AWAITING_PLAN_APPROVAL",
      workspacePath: "/tmp/workspace",
      backendBaseUrl: "http://127.0.0.1:8000",
      updatedAt: "2026-03-03T00:00:00.000Z",
    };

    const controller = new CrucibleController(
      () => backend,
      store,
      createSettings(),
      createUi({
        showInfo: (message) => infos.push(message),
      }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-03-03T00:00:00.000Z"
    );

    await controller.initialize();
    await controller.providePlanFeedback("   ");
    await controller.providePlanFeedback("Please scope this to the API layer");
    controller.dispose();

    expect(state.planFeedbackCalls).toEqual([
      { taskId: "task-plan", feedback: null },
      { taskId: "task-plan", feedback: "Please scope this to the API layer" },
    ]);
    expect(infos.some((message) => message.includes("Plan approved"))).toBe(true);
    expect(infos.some((message) => message.includes("Submitted plan feedback"))).toBe(true);
  });

  test("handlePlanCardAction feedback forwards planning tool calls/results as structured events", async () => {
    const state: StubBackendState = {
      submitPayloads: [],
      getTaskCalls: [],
      acceptCalls: [],
      rejectCalls: [],
      getResultCalls: [],
      planFeedbackCalls: [],
    };
    const toolEvents: Array<{ id: number; tool: string; source: string }> = [];
    const toolResults: Array<{ id: number; output: string; isError: boolean }> = [];

    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      streamPatch: async (_taskId, onEvent, _signal) => {
        onEvent({
          type: "planning_tool_call",
          payload: { tool: "search_code", thought: "scan auth call sites", iteration: 1, args: { pattern: "auth" } },
        });
        onEvent({
          type: "planning_tool_result",
          payload: { tool: "search_code", output: "3 matches", is_error: false, iteration: 1 },
        });
        onEvent({
          type: "planning_complete",
          payload: { files_examined: ["src/auth.py"], confidence: "high" },
        });
      },
    };

    const store = new MemorySessionStore();
    const controller = new CrucibleController(
      () => backend,
      store,
      createSettings(),
      createUi({
        appendToolEvent: (e) => toolEvents.push({ id: e.id, tool: e.tool, source: e.source }),
        appendToolResult: (id, output, isError) => toolResults.push({ id, output, isError }),
      }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-03-03T00:00:00.000Z"
    );

    await controller.handlePlanCardAction("task-plan", "feedback", "Scope it to the API layer");
    controller.dispose();

    expect(state.planFeedbackCalls).toEqual([
      { taskId: "task-plan", feedback: "Scope it to the API layer" },
    ]);
    expect(toolEvents).toHaveLength(1);
    expect(toolEvents[0].tool).toBe("search_code");
    expect(toolEvents[0].source).toBe("planning");
    expect(toolResults).toHaveLength(1);
    expect(toolResults[0].id).toBe(toolEvents[0].id);
    expect(toolResults[0].output).toBe("3 matches");
    expect(toolResults[0].isError).toBe(false);
  });

});

describe("CrucibleController — chat", () => {
  test("sendChatMessage appends user message and streams agent response", async () => {
    const appendedMessages: Array<{ role: string; content: string }> = [];
    const chunks: string[] = [];

    const chatBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      createChatThread: async (workspacePath) => ({
        threadId: "chat-new",
        workspacePath,
        title: "New Chat",
        createdAt: "2026-05-11T00:00:00Z",
      }),
      listChatThreads: async () => [],
      getChatThread: async (threadId) => ({
        threadId,
        workspacePath: "/tmp/workspace",
        title: "New Chat",
        messages: [],
        touchedFiles: [],
      }),
      sendChatMessage: asStream(async function* (_threadId: string, _message: string, _signal?: AbortSignal) {
        yield { type: "chat_agent_thinking" as const, payload: { message: "Exploring…" } };
        yield { type: "intent_classified" as const, payload: { intent: "qa", rationale: "", likely_targets: [] } };
        yield { type: "chat_response" as const, payload: { chunk: "The answer is 42." } };
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      }),
    };

    const store = new MemorySessionStore();
    const controller = new CrucibleController(
      () => chatBackend,
      store,
      createSettings(),
      createUi({
        appendChatMessage: (m) => appendedMessages.push({ role: m.role, content: m.content }),
        appendChatChunk: (c) => chunks.push(c),
      }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );

    await controller.sendChatMessage("What is the answer?");
    controller.dispose();

    expect(appendedMessages[0].role).toBe("user");
    expect(appendedMessages[0].content).toBe("What is the answer?");
    expect(chunks).toContain("The answer is 42.");
  });

  test("streamTurn relays retry_status to ui.updateRetryStatus and clears it in the finally block", async () => {
    const retryCalls: Array<{ attempt: number; max_attempts: number; reason: string; message: string } | null> = [];

    const chatBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      createChatThread: async (workspacePath) => ({
        threadId: "chat-new", workspacePath, title: "New Chat", createdAt: "2026-07-14T00:00:00Z",
      }),
      listChatThreads: async () => [],
      getChatThread: async (threadId) => ({
        threadId, workspacePath: "/tmp/workspace", title: "New Chat", messages: [], touchedFiles: [],
      }),
      sendChatMessage: asStream(async function* (_threadId: string, _message: string, _signal?: AbortSignal) {
        yield {
          type: "retry_status" as const,
          payload: { attempt: 1, max_attempts: 4, reason: "rate_limited", message: "⏳ retrying…" },
        };
        yield { type: "chat_response" as const, payload: { chunk: "hi" } };
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      }),
    };

    const controller = new CrucibleController(
      () => chatBackend, new MemorySessionStore(), createSettings(),
      createUi({ updateRetryStatus: (status) => retryCalls.push(status) }),
      { openDiff: async () => {} },
      () => "2026-07-14T00:00:00.000Z"
    );

    await controller.sendChatMessage("hello");
    controller.dispose();

    expect(retryCalls).toContainEqual({
      attempt: 1, max_attempts: 4, reason: "rate_limited", message: "⏳ retrying…",
    });
    // finally-block cleanup: the last call must clear it back to null
    expect(retryCalls[retryCalls.length - 1]).toBeNull();
  });

  test("streamTurn relays chat_progress as a progress-tagged agent message", async () => {
    const appendedMessages: ChatMessage[] = [];

    const chatBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      createChatThread: async (workspacePath) => ({
        threadId: "chat-new", workspacePath, title: "New Chat", createdAt: "2026-07-25T00:00:00Z",
      }),
      listChatThreads: async () => [],
      getChatThread: async (threadId) => ({
        threadId, workspacePath: "/tmp/workspace", title: "New Chat", messages: [], touchedFiles: [],
      }),
      sendChatMessage: asStream(async function* (_threadId: string, _message: string, _signal?: AbortSignal) {
        yield {
          type: "chat_progress" as const,
          payload: { note: "Working on task 1." },
        };
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      }),
    };

    const controller = new CrucibleController(
      () => chatBackend, new MemorySessionStore(), createSettings(),
      createUi({ appendChatMessage: (m) => appendedMessages.push(m) }),
      { openDiff: async () => {} },
      () => "2026-07-25T00:00:00.000Z"
    );

    await controller.sendChatMessage("hello");
    controller.dispose();

    const progressMsg = appendedMessages.find((m) => m.metadata?.progress === true);
    expect(progressMsg).toBeDefined();
    expect(progressMsg?.content).toBe("Working on task 1.");
    expect(progressMsg?.role).toBe("agent");
    expect(progressMsg?.type).toBe("text");
  });

  test("sendChatMessage with mentionedPaths tags the optimistic echo with mentioned_files metadata", async () => {
    const appendedMessages: ChatMessage[] = [];
    const workspacePath = fs.mkdtempSync(path.join(os.tmpdir(), "mention-ctrl-"));
    fs.writeFileSync(path.join(workspacePath, "a.py"), "x = 1");

    const chatBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      createChatThread: async (ws) => ({
        threadId: "chat-new", workspacePath: ws, title: "New Chat",
        createdAt: "2026-05-11T00:00:00Z",
      }),
      listChatThreads: async () => [],
      getChatThread: async (threadId) => ({
        threadId, workspacePath, title: "New Chat", messages: [], touchedFiles: [],
      }),
      sendChatMessage: asStream(async function* () {
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      }),
    };

    const store = new MemorySessionStore();
    const controller = new CrucibleController(
      () => chatBackend,
      store,
      createSettings(),
      createUi({
        getWorkspacePath: () => workspacePath,
        appendChatMessage: (m) => appendedMessages.push(m),
      }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );

    await controller.sendChatMessage("look at @a.py", undefined, undefined, ["a.py"]);
    controller.dispose();

    expect(appendedMessages[0].metadata).toEqual({ mentioned_files: ["a.py"] });
  });

  test("thinking entries: chat_agent_thinking appends entry; explore_tool_call forwards structured tool event, finally hides indicator", async () => {
    const thinkingEntries: string[] = [];
    const toolEvents: Array<{ id: number; tool: string; source: string }> = [];
    let hideCalled = false;

    const chatBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      createChatThread: async (workspacePath) => ({
        threadId: "chat-th",
        workspacePath,
        title: "New Chat",
        createdAt: "2026-05-11T00:00:00Z",
      }),
      listChatThreads: async () => [],
      getChatThread: async (threadId) => ({
        threadId, workspacePath: "/tmp/workspace",
        title: "New Chat", messages: [], touchedFiles: [],
      }),
      sendChatMessage: asStream(async function* (_threadId: string, _message: string, _signal?: AbortSignal) {
        yield { type: "chat_agent_thinking" as const, payload: { message: "Exploring workspace…" } };
        yield { type: "explore_tool_call" as const, payload: { tool: "search_code", args: { pattern: "auth" }, thought: "Looking for auth handling code" } };
        yield { type: "intent_classified" as const, payload: { intent: "qa", rationale: "", likely_targets: [] } };
        yield { type: "chat_response" as const, payload: { chunk: "It handles auth." } };
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      }),
    };

    const store = new MemorySessionStore();
    const controller = new CrucibleController(
      () => chatBackend,
      store,
      createSettings(),
      createUi({
        appendChatThinkingEntry: (t) => thinkingEntries.push(t),
        appendToolEvent: (e) => toolEvents.push({ id: e.id, tool: e.tool, source: e.source }),
        hideChatThinking: () => { hideCalled = true; },
      }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );

    await controller.sendChatMessage("What does auth do?");
    controller.dispose();

    // chat_agent_thinking still appends a thinking entry
    expect(thinkingEntries[0]).toBe("Exploring workspace…");
    // explore_tool_call is now forwarded as a structured tool event, not a thinking entry
    expect(toolEvents).toHaveLength(1);
    expect(toolEvents[0].tool).toBe("search_code");
    expect(toolEvents[0].source).toBe("explore");
    expect(hideCalled).toBe(true);
  });

  test("handleEditDecisionFromChat posts the edit decision (plain POST, no stream consume)", async () => {
    const editCalls: Array<{ threadId: string; decision: string; reason: string; gateId?: string }> = [];

    const editBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      postEditDecision: async (threadId, decision, reason, gateId) => {
        editCalls.push({ threadId, decision, reason: reason ?? "", gateId });
      },
    };

    const store = new MemorySessionStore();
    const controller = new CrucibleController(
      () => editBackend,
      store,
      createSettings(),
      createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );

    await controller.handleEditDecisionFromChat("thread-1", "accept", "looks good", "g-edit");
    controller.dispose();

    expect(editCalls).toEqual([
      { threadId: "thread-1", decision: "accept", reason: "looks good", gateId: "g-edit" },
    ]);
  });
});

describe("CrucibleController — tool-event pairing & orphan handling", () => {
  test("results pair with their matching source; orphan result after finally-clear is dropped", async () => {
    // IDs assigned by forwardToolCall, keyed by source, so we can verify cross-source pairing.
    const toolEventIds: number[] = [];
    const toolResultIds: number[] = [];

    // Turn 1: explore call + result, execution call + result.
    const turn1Backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      createChatThread: async (workspacePath) => ({
        threadId: "chat-pair",
        workspacePath,
        title: "New Chat",
        createdAt: "2026-06-10T00:00:00Z",
      }),
      listChatThreads: async () => [],
      getChatThread: async (threadId) => ({
        threadId, workspacePath: "/tmp/workspace",
        title: "New Chat", messages: [], touchedFiles: [],
      }),
      sendChatMessage: asStream(async function* (_threadId, _message, _signal) {
        yield { type: "explore_tool_call" as const, payload: { tool: "search_code", args: {}, thought: "looking" } };
        yield { type: "tool_call" as const, payload: { tool: "read_file", thought: "", iteration: 1, phase: "explore", args: {} } };
        yield { type: "explore_tool_result" as const, payload: { tool: "search_code", output: "explore-out", is_error: false } };
        yield { type: "tool_result" as const, payload: { tool: "read_file", output: "exec-out", is_error: false, iteration: 1 } };
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      }),
    };

    const store = new MemorySessionStore();
    const controller = new CrucibleController(
      () => turn1Backend, store, createSettings(),
      createUi({
        appendToolEvent: (e) => { toolEventIds.push(e.id); },
        appendToolResult: (id) => { toolResultIds.push(id); },
      }),
      { openDiff: async () => {} },
      () => "2026-06-10T00:00:00.000Z"
    );

    await controller.sendChatMessage("first turn");

    // Both calls got events; both results paired to their call ids.
    expect(toolEventIds).toHaveLength(2);
    expect(toolResultIds).toHaveLength(2);
    expect(toolResultIds).toEqual(expect.arrayContaining(toolEventIds)); // each result ids a call

    // Turn 2: a stray tool_result arrives as the very first event — should be dropped
    // because finally cleared openToolEvent at the end of turn 1.
    const strayResultIds: number[] = [];
    const turn2Backend: BackendTaskClient = {
      ...turn1Backend,
      sendChatMessage: asStream(async function* (_threadId, _message, _signal) {
        // No preceding tool_call — orphan result.
        yield { type: "tool_result" as const, payload: { tool: "read_file", output: "stray", is_error: false, iteration: 1 } };
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      }),
    };
    const controller2 = new CrucibleController(
      () => turn2Backend, store, createSettings(),
      createUi({
        appendToolResult: (id) => { strayResultIds.push(id); },
      }),
      { openDiff: async () => {} },
      () => "2026-06-10T00:00:00.000Z"
    );
    // Pre-populate activeThreadId so no createChatThread call is made.
    await controller2.switchChatThread("chat-pair");
    await controller2.sendChatMessage("second turn — stray result");

    expect(strayResultIds).toHaveLength(0); // orphan dropped
    controller.dispose();
    controller2.dispose();
  });
});

describe("CrucibleController — abort handling", () => {
  test("AbortError is swallowed; non-abort error re-enables input and is surfaced via showError", async () => {
    let inputEnabledFinalValue: boolean | undefined;
    const errors: string[] = [];

    const abortBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      createChatThread: async (workspacePath) => ({
        threadId: "chat-abort", workspacePath,
        title: "New Chat", createdAt: "2026-06-10T00:00:00Z",
      }),
      listChatThreads: async () => [],
      getChatThread: async (threadId) => ({
        threadId, workspacePath: "/tmp/workspace",
        title: "New Chat", messages: [], touchedFiles: [],
      }),
      sendChatMessage: asStream(async function* (_threadId, _message, _signal) {
        yield { type: "chat_agent_thinking" as const, payload: { message: "thinking…" } };
        throw Object.assign(new Error("aborted"), { name: "AbortError" });
      }),
    };

    const controller = new CrucibleController(
      () => abortBackend, new MemorySessionStore(), createSettings(),
      createUi({
        showError: (msg) => errors.push(msg),
        setChatInputEnabled: (enabled) => { inputEnabledFinalValue = enabled; },
      }),
      { openDiff: async () => {} },
      () => "2026-06-10T00:00:00.000Z"
    );

    // AbortError must resolve cleanly (not throw) and must NOT call showError.
    await expect(controller.sendChatMessage("stop me")).resolves.toBeUndefined();
    expect(errors).toHaveLength(0);
    // finally always re-enables input regardless of error type.
    expect(inputEnabledFinalValue).toBe(true);
    controller.dispose();

    // Non-abort error IS surfaced: the catch/finally re-enables input; the error itself
    // is rethrown and the caller (extension.ts command handler) surfaces it via showError.
    // In the test harness the rethrow surfaces as a rejected promise.
    const nonAbortBackend: BackendTaskClient = {
      ...abortBackend,
      sendChatMessage: asStream(async function* (_threadId, _message, _signal) {
        yield { type: "chat_agent_thinking" as const, payload: { message: "thinking…" } };
        throw new Error("backend exploded");
      }),
    };
    let finalEnabled2: boolean | undefined;
    const controller2 = new CrucibleController(
      () => nonAbortBackend, new MemorySessionStore(), createSettings(),
      createUi({
        setChatInputEnabled: (enabled) => { finalEnabled2 = enabled; },
      }),
      { openDiff: async () => {} },
      () => "2026-06-10T00:00:00.000Z"
    );
    await controller2.switchChatThread("chat-abort");
    await expect(controller2.sendChatMessage("boom")).rejects.toThrow("backend exploded");
    expect(finalEnabled2).toBe(true); // finally still re-enables input
    controller2.dispose();
  });
});

describe("CrucibleController — deviation capture", () => {
  test("scope breadcrumb is captured; accepted-step breadcrumb is not", async () => {
    const taskId = "task-dev-1";

    const breadcrumbBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      streamPatchEvents: async function* (_taskId: string) {
        yield {
          type: "chat_breadcrumb" as const,
          payload: { text: "✓ Scope extension approved: x.py", task_id: taskId },
        };
        yield {
          type: "chat_breadcrumb" as const,
          payload: { text: "✓ Step changes accepted: t", task_id: taskId },
        };
        yield { type: "done" as const, payload: { status: "SUCCEEDED" } };
      },
    };

    const controller = new CrucibleController(
      () => breadcrumbBackend, new MemorySessionStore(), createSettings(),
      createUi(),
      { openDiff: async () => {} },
      () => "2026-06-10T00:00:00.000Z"
    );

    await controller.streamTaskIntoChatThread(taskId);

    expect(controller.observedDeviations).toContain("✓ Scope extension approved: x.py");
    expect(controller.observedDeviations).not.toContain("✓ Step changes accepted: t");
    controller.dispose();
  });
});


describe("CrucibleController — chat_progress rendering", () => {
  test("renders chat_progress as a progress-tagged agent message via streamTaskIntoChatThread", async () => {
    const taskId = "task-progress-1";
    const messages: ChatMessage[] = [];

    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      streamPatchEvents: async function* (_taskId: string) {
        yield {
          type: "chat_progress" as const,
          payload: { note: "Working on task 1." },
        };
        yield { type: "done" as const, payload: { status: "SUCCEEDED" } };
      },
    };

    const controller = new CrucibleController(
      () => backend,
      new MemorySessionStore(),
      createSettings(),
      createUi({ appendChatMessage: (m) => messages.push(m) }),
      { openDiff: async () => {} },
      () => "2026-07-25T00:00:00.000Z"
    );

    await controller.streamTaskIntoChatThread(taskId);
    controller.dispose();

    const progressMsg = messages.find((m) => m.metadata?.progress === true);
    expect(progressMsg).toBeDefined();
    expect(progressMsg?.content).toBe("Working on task 1.");
    expect(progressMsg?.role).toBe("agent");
    expect(progressMsg?.type).toBe("text");
  });
});

describe("CrucibleController — resume streaming", () => {
  const resumeBackend = (
    onStream: (taskId: string) => void,
    events: Array<{ type: string; payload: Record<string, unknown> }>,
  ): BackendTaskClient => ({
    ...createStubBackend({
      submitPayloads: [], getTaskCalls: [], acceptCalls: [],
      rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
    }),
    resumeTask: async (parentId) => ({ taskId: "task-child", resumeOfTaskId: parentId }),
    streamPatchEvents: async function* (taskId: string) {
      onStream(taskId);
      for (const e of events) yield e as never;
    },
  });

  test("execute-stage resume streams the child channel and anchors a task_card", async () => {
    const streamed: string[] = [];
    const toolEvents: Array<{ source: string }> = [];
    const workbars: Array<{ stepIndex?: number } | null> = [];
    const messages: ChatMessage[] = [];

    const backend = resumeBackend((id) => streamed.push(id), [
      { type: "tool_call", payload: { tool: "read_file", thought: "", iteration: 1, phase: "explore", args: { path: "x.py" } } },
      { type: "step_started", payload: { step_id: "s1", step_title: "Do s1", step_index: 1, total_steps: 3 } },
      { type: "done", payload: { status: "SUCCEEDED" } },
    ]);
    const ui = createUi({
      appendToolEvent: (e) => toolEvents.push({ source: e.source }),
      updateWorkbar: (info) => workbars.push(info as { stepIndex?: number } | null),
      appendChatMessage: (m) => messages.push(m),
    });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async () => {} },
      () => "2026-06-13T00:00:00.000Z"
    );

    await controller.resumeTaskById("task-parent", "execute");

    expect(streamed).toContain("task-child");
    expect(toolEvents.some((e) => e.source === "execution")).toBe(true);
    expect(workbars.some((w) => w?.stepIndex === 1)).toBe(true);
    expect(messages.some((m) => m.type === "task_card" && m.taskId === "task-child")).toBe(true);
    controller.dispose();
  });

  test("plan-stage resume does NOT stream the child channel (driven by /live)", async () => {
    const streamed: string[] = [];
    const backend = resumeBackend((id) => streamed.push(id), [
      { type: "done", payload: {} },
    ]);
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async () => {} },
      () => "2026-06-13T00:00:00.000Z"
    );

    await controller.resumeTaskById("task-parent", "plan");

    expect(streamed).not.toContain("task-child");
    controller.dispose();
  });
});

describe("CrucibleController — command-decision", () => {
  test("handleCommandDecisionFromChat posts the decision to the backend", async () => {
    const sent: Array<{ taskId: string; decision: CommandDecision }> = [];
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      sendCommandDecision: async (taskId, decision) => {
        sent.push({ taskId, decision });
        return { taskId, status: "EXECUTING" as const };
      },
    };
    const store = new MemorySessionStore();
    store.value = {
      taskId: "task-1",
      status: "AWAITING_COMMAND_DECISION",
      workspacePath: "/tmp/workspace",
      backendBaseUrl: "http://127.0.0.1:8000",
      updatedAt: "2026-05-28T00:00:00.000Z",
    };
    const controller = new CrucibleController(
      () => backend,
      store,
      createSettings(),
      createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.initialize();

    await controller.handleCommandDecisionFromChat("task-1", {
      approve: true, remember: true, scope: "prefix", ruleValue: "python -c",
    }, "task:task-1:command");
    controller.dispose();

    expect(sent).toEqual([{
      taskId: "task-1",
      decision: { approve: true, remember: true, scope: "prefix", ruleValue: "python -c" },
    }]);
  });

  test("handleCommandDecisionFromChat routes a controller gate (no task) to the chat endpoint", async () => {
    // A controller EDIT-turn command gate has NO task: /live.activeTaskId is null and the
    // gate id is the thread id. The decision must go to the CHAT command-decision route,
    // never the task route (which would 404 on the thread id).
    const chatSent: Array<{ threadId: string; decision: CommandDecision; gateId?: string }> = [];
    const taskSent: Array<{ taskId: string; decision: CommandDecision }> = [];
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: NULL_LIVE_STATE,
    };
    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      sendCommandDecision: async (taskId, decision) => {
        taskSent.push({ taskId, decision });
        return { taskId, status: "EXECUTING" as const };
      },
      postChatCommandDecision: async (threadId, decision, gateId) => {
        chatSent.push({ threadId, decision, gateId });
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("thread-9");
    await Promise.resolve();
    controller.dispose();
    // A controller command gate: no task, gate id is the thread.
    state.liveResponse = {
      activeTaskId: null, status: null, plan: null, turnActive: true,
      pendingGates: [{ gateId: "g-cmd", kind: "command", payload: { command: "rm", args: ["-rf", "x"] }, agent: null }],
    };
    await controller.pollThreadLiveState();  // sets latestLiveState (activeTaskId null)

    await controller.handleCommandDecisionFromChat("thread-9", {
      approve: true, remember: false, scope: "exact",
    }, "g-cmd");

    expect(chatSent).toEqual([{
      threadId: "thread-9",
      decision: { approve: true, remember: false, scope: "exact" },
      gateId: "g-cmd",
    }]);
    expect(taskSent).toEqual([]);  // NOT the task route
  });

  test("stopActiveTurn optimistically appends a ✗ Stopped breadcrumb (live broadcast is missed)", async () => {
    // Stop aborts the FE SSE reader before POST /stop, so the backend's live
    // chat_breadcrumb never reaches the open webview (finding 7). Append it optimistically
    // — same pattern as the accept/discard breadcrumbs. Gated on ok (a real stop).
    const messages: ChatMessage[] = [];
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: NULL_LIVE_STATE,
    };
    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      stopChatTurn: async () => ({ ok: true }),
    };
    const ui = createUi({ appendChatMessage: (m) => messages.push(m) });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("thread-1");
    await Promise.resolve();
    controller.dispose();
    messages.length = 0;  // ignore the thread's loaded messages

    await controller.stopActiveTurn();

    const crumb = messages.find(
      (m) => m.content === "✗ Stopped" && m.metadata?.breadcrumb === true);
    expect(crumb).toBeDefined();
  });

  test("stopActiveTurn does NOT append a breadcrumb when stop is a no-op (ok:false)", async () => {
    const messages: ChatMessage[] = [];
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: NULL_LIVE_STATE,
    };
    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      stopChatTurn: async () => ({ ok: false }),
    };
    const ui = createUi({ appendChatMessage: (m) => messages.push(m) });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("thread-1");
    await Promise.resolve();
    controller.dispose();
    messages.length = 0;

    await controller.stopActiveTurn();

    expect(messages.some((m) => m.content === "✗ Stopped")).toBe(false);
  });

  test("pollThreadLiveState renders one gate card, dedups, and removes on null", async () => {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: NULL_LIVE_STATE,
    };
    const backend = createStubBackend(state);

    const gateRenders: LiveGateView[][] = [];
    const planRenders: LivePlanView[] = [];
    let gateClears = 0;
    let planClears = 0;
    const ui = createUi({
      renderLiveGates: (gates) => { gateRenders.push(gates); },
      clearLiveGates: () => { gateClears += 1; },
      renderLivePlan: (plan) => { planRenders.push(plan); },
      clearLivePlan: () => { planClears += 1; },
    });

    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );

    // Establish an active thread, then stop the auto-poll timer so we drive ticks by hand.
    await controller.switchChatThread("chat-1");
    await Promise.resolve();
    controller.dispose();
    gateRenders.length = 0; planRenders.length = 0; gateClears = 0; planClears = 0;

    // Poll #1: a command gate appears → exactly one gate card rendered.
    state.liveResponse = {
      activeTaskId: "task-1",
      status: "AWAITING_COMMAND_DECISION",
      pendingGates: [{ gateId: "task:task-1:command", kind: "command", payload: { command: "pytest" }, agent: null }],
      plan: null,
      turnActive: false,
    };
    await controller.pollThreadLiveState();
    expect(gateRenders).toHaveLength(1);
    expect(gateRenders[0][0].kind).toBe("command");
    expect(gateRenders[0][0].taskId).toBe("task-1");
    expect(gateRenders[0][0].payload.command).toBe("pytest");

    // Poll #2: identical state → dedup, no second render (replace-not-append stays one card).
    await controller.pollThreadLiveState();
    expect(gateRenders).toHaveLength(1);

    // Poll #3: gate resolved (null) → card removed.
    state.liveResponse = NULL_LIVE_STATE;
    await controller.pollThreadLiveState();
    expect(gateClears).toBe(1);
  });

  test("pollThreadLiveState renders every pending gate, addressing each to its owner", async () => {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: NULL_LIVE_STATE,
    };
    const backend = createStubBackend(state);
    const renders: LiveGateView[][] = [];
    const ui = createUi({ renderLiveGates: (gates) => { renders.push(gates); } });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-1");
    await Promise.resolve();
    controller.dispose();
    renders.length = 0;

    const agent = { id: "agent-1", label: "impl", name: "general" };
    state.liveResponse = {
      activeTaskId: "task-1", status: "AWAITING_COMMAND_DECISION", plan: null,
      turnActive: true,
      pendingGates: [
        { gateId: "g-mcp", kind: "mcp_tool", payload: {}, agent },
        { gateId: "task:task-1:command", kind: "command", payload: { command: "ls" }, agent: null },
      ],
    };
    await controller.pollThreadLiveState();
    expect(renders).toHaveLength(1);
    expect(renders[0].map((g) => [g.gateId, g.taskId, g.agent?.label ?? null])).toEqual([
      ["g-mcp", "chat-1", "impl"],
      ["task:task-1:command", "task-1", null],
    ]);
  });

  test("a lost-race team plan decision is swallowed", async () => {
    const state = { submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
                    getResultCalls: [], planFeedbackCalls: [] };
    const errors: string[] = [];
    const calls: unknown[][] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      decideTeamPlan: async (...args: unknown[]) => {
        calls.push(args);
        throw Object.assign(new Error("gone"), { status: 404 });
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(),
      createUi({ showError: (m: string) => { errors.push(m); } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z");
    await controller.decideTeamPlan("chat-1", "g1", "approve");
    expect(calls).toEqual([["chat-1", "g1", "approve", undefined]]);
    expect(errors).toEqual([]);
    controller.dispose();
  });

  test("a gate decision that lost the race (404) is swallowed, not shown as an error", async () => {
    const errors: string[] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      postChatMcpDecision: async () => {
        throw Object.assign(new Error("no pending mcp_tool gate"), { status: 404 });
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(),
      createUi({ showError: (m: string) => { errors.push(m); } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.handleMcpDecisionFromChat("chat-1", { approve: true, remember: false }, "g-gone");
    controller.dispose();
    expect(errors).toEqual([]);
  });

  test("pollThreadLiveState renders the session strip, dedups stable rows, re-renders on change", async () => {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: NULL_LIVE_STATE,
    };
    const backend = createStubBackend(state);
    const sessionRenders: LiveSessionsView[] = [];
    let sessionClears = 0;
    const ui = createUi({
      renderLiveSessions: (v) => { sessionRenders.push(v); },
      clearLiveSessions: () => { sessionClears += 1; },
    });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-1");
    await Promise.resolve();
    controller.dispose();
    sessionRenders.length = 0; sessionClears = 0;

    const row = {
      id: "sess-1", command: "python -m http.server 8765",
      status: "running" as const, exit_code: null, started_at: 1720000000,
    };
    state.liveResponse = { ...NULL_LIVE_STATE, sessions: [row] };
    await controller.pollThreadLiveState();
    expect(sessionRenders).toHaveLength(1);
    expect(sessionRenders[0].items[0].id).toBe("sess-1");

    // Identical rows → dedup (possible ONLY because rows are stable: no ticking
    // age_sec/unread_bytes — the review-fix #2 invariant).
    await controller.pollThreadLiveState();
    expect(sessionRenders).toHaveLength(1);

    // A real change (running→exited) re-renders — proves sessions is IN the signature.
    state.liveResponse = {
      ...NULL_LIVE_STATE,
      sessions: [{ ...row, status: "exited" as const, exit_code: 0 }],
    };
    await controller.pollThreadLiveState();
    expect(sessionRenders).toHaveLength(2);
    expect(sessionRenders[1].items[0].status).toBe("exited");

    // No sessions → strip clears.
    state.liveResponse = NULL_LIVE_STATE;
    await controller.pollThreadLiveState();
    expect(sessionClears).toBeGreaterThan(0);
  });

  test("fetchSessionTranscript resolves via the active chat thread and nulls on error", async () => {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [],
    };
    const transcriptCalls: Array<{ threadId: string; sessionId: string }> = [];
    const backend = {
      ...createStubBackend(state),
      getSessionTranscript: async (threadId: string, sessionId: string) => {
        transcriptCalls.push({ threadId, sessionId });
        if (sessionId === "sess-gone") throw new Error("404");
        return {
          output_tail: "Serving HTTP on :: port 8765",
          stdin_history: [{ ts: 1, chars: "y\n" }],
          status: "running" as const,
          exit_code: null,
        };
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-9");
    await Promise.resolve();
    controller.dispose();

    const t = await controller.fetchSessionTranscript("sess-1");
    expect(t?.output_tail).toContain("Serving HTTP");
    expect(transcriptCalls[0]).toEqual({ threadId: "chat-9", sessionId: "sess-1" });

    const missing = await controller.fetchSessionTranscript("sess-gone");
    expect(missing).toBeNull();
  });

  test("pollThreadLiveState pushes turnActive false→true→false even when card signature is unchanged", async () => {
    // A controller chat turn has NO task: status/gate/plan/runSummary are all null and
    // never change across the turn, so the card-dedup signature is constant. If turnActive
    // is omitted from that signature, the turn-end transition (true→false) is deduped away
    // and sendLiveStatus(..., false) is never delivered → the composer stays wedged on
    // "Agent is working…" forever. turnActive MUST be part of the signature.
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: NULL_LIVE_STATE,
    };
    const backend = createStubBackend(state);

    const turnActives: boolean[] = [];
    const ui = createUi({
      sendLiveStatus: (_status, turnActive) => { turnActives.push(turnActive); },
    });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-1");
    await Promise.resolve();
    controller.dispose();
    turnActives.length = 0;

    // Poll #1: controller turn running (no task, no gate) → turnActive=true delivered.
    state.liveResponse = {
      activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: true,
    };
    await controller.pollThreadLiveState();

    // Poll #2: turn ended — ONLY turnActive changed (signature otherwise identical).
    state.liveResponse = {
      activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: false,
    };
    await controller.pollThreadLiveState();

    expect(turnActives).toEqual([true, false]);
  });

  test("live-resume: re-subscribes once on a fresh reload (turn_active, no local stream)", async () => {
    // A fresh webview after a reload has no local stream (turnAbort === null) but /live
    // reports turn_active=true for the detached turn → resume re-subscribes exactly once;
    // _liveResumeThreadId keeps later polls idempotent.
    let streamChannelCalls = 0;
    let release!: () => void;
    const blocker = new Promise<void>((r) => { release = r; });
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: {
        activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: true,
      },
    };
    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      streamChannel: async function* (_channelId: string) {
        streamChannelCalls++;
        await blocker; // keep the resume stream open so turnAbort stays set
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-resume"); // first poll fires the resume
    await controller.pollThreadLiveState();           // _liveResumeThreadId set → no re-fire
    await new Promise((r) => setTimeout(r, 0));        // flush the resume's first .next()
    expect(streamChannelCalls).toBe(1);
    release();
    controller.dispose();
  });

  test("live-resume: does NOT re-subscribe while a local turn stream is active (turnAbort guard)", async () => {
    // The real double-render bug: during a normal sendChatMessage turn, turnAbort is set
    // and /live reports turn_active=true while _liveResumeThreadId is still null. Without
    // the `turnAbort === null` guard the 1s poll opens a SECOND streamTurn on the same
    // channel (broadcaster replays to every subscriber) → every event renders twice.
    let streamChannelCalls = 0;
    let releaseSend!: () => void;
    const sendBlock = new Promise<void>((r) => { releaseSend = r; });
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: {
        activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: false,
      },
    };
    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      sendChatMessage: asStream(async function* () {
        await sendBlock; // hold the local turn open so turnAbort stays set
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      }),
      streamChannel: async function* (_channelId: string) {
        streamChannelCalls++;
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-active"); // poll sees turn_active=false → no resume
    const sendPromise = controller.sendChatMessage("hi"); // sets turnAbort, blocks on the stub
    await new Promise((r) => setTimeout(r, 0));            // let sendChatMessage set turnAbort
    state.liveResponse = {
      activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: true,
    };
    await controller.pollThreadLiveState();               // turnAbort set → resume must NOT fire
    await new Promise((r) => setTimeout(r, 0));
    expect(streamChannelCalls).toBe(0);
    releaseSend();
    await sendPromise;
    controller.dispose();
  });

  test("pollThreadLiveState surfaces a plan card at AWAITING_PLAN_APPROVAL", async () => {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: {
        activeTaskId: "task-9",
        status: "AWAITING_PLAN_APPROVAL",
        pendingGates: [],
        plan: { task_id: "task-9", plan_markdown: "# Plan\n- step" },
        turnActive: false,
      },
    };
    const backend = createStubBackend(state);
    const planRenders: LivePlanView[] = [];
    const ui = createUi({ renderLivePlan: (plan) => { planRenders.push(plan); } });

    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-1");
    await controller.pollThreadLiveState();
    controller.dispose();

    expect(planRenders.length).toBeGreaterThanOrEqual(1);
    expect(planRenders[planRenders.length - 1].taskId).toBe("task-9");
    expect(planRenders[planRenders.length - 1].planMarkdown).toContain("# Plan");
  });

  test("live review derivation: READY_FOR_REVIEW renders review card with stepsTotal and sendLiveStatus", async () => {
    let reviewRendered: Parameters<ControllerUI["renderLiveReview"]>[0] | null = null;
    const liveStatuses: Array<string | null> = [];

    const reviewBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      getThreadLiveState: async () => ({
        activeTaskId: "t9",
        status: "READY_FOR_REVIEW",
        pendingGates: [],
        plan: null,
        turnActive: false,
      }),
      getTaskResult: async (_taskId) => ({
        taskId: "t9",
        status: "READY_FOR_REVIEW",
        modifiedFiles: ["a.py"],
        diagnostics: [],
        shadowWorkspacePath: "/shadow",
        plan: {
          analysis: "a",
          steps: [
            { id: "s1", goal: "g1", targets: [{ path: "a.py", intent: "existing" as const }], risk: "low" as const },
            { id: "s2", goal: "g2", targets: [{ path: "a.py", intent: "existing" as const }], risk: "low" as const },
          ],
          expected_files: ["a.py"],
          stop_conditions: ["done"],
        },
        patch: { patch_ops: [] },
      } as TaskResult),
    };

    const ui = createUi({
      renderLiveReview: (review) => { reviewRendered = review; },
      sendLiveStatus: (status) => { liveStatuses.push(status); },
    });

    const controller = new CrucibleController(
      () => reviewBackend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-rev");
    await controller.pollThreadLiveState();
    controller.dispose();

    expect(reviewRendered).not.toBeNull();
    expect(reviewRendered!.taskId).toBe("t9");
    expect(reviewRendered!.modifiedFiles).toEqual(["a.py"]);
    expect(reviewRendered!.stepsTotal).toBe(2);
    expect(liveStatuses).toContain("READY_FOR_REVIEW");
  });

  test("live error derivation: FAILED status renders error card; non-FAILED clears it", async () => {
    let errorRendered: Parameters<ControllerUI["renderLiveError"]>[0] | null = null;
    let errorClears = 0;

    const failedBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      getThreadLiveState: async () => ({
        activeTaskId: "task-fail",
        status: "FAILED",
        pendingGates: [],
        plan: null,
        turnActive: false,
      }),
    };

    const ui = createUi({
      renderLiveError: (error) => { errorRendered = error; },
      clearLiveError: () => { errorClears += 1; },
    });

    const controller = new CrucibleController(
      () => failedBackend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-fail");
    await controller.pollThreadLiveState();
    controller.dispose();

    expect(errorRendered).not.toBeNull();
    expect(errorRendered!.taskId).toBe("task-fail");
    expect(errorRendered!.status).toBe("FAILED");
    // non-FAILED status clears the error
    expect(errorClears).toBe(0); // first poll was FAILED so no clear

    // Second controller with non-FAILED status: clearLiveError should fire
    let errorClears2 = 0;
    const okBackend: BackendTaskClient = {
      ...failedBackend,
      getThreadLiveState: async () => ({ activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: false }),
    };
    const controller2 = new CrucibleController(
      () => okBackend, new MemorySessionStore(), createSettings(),
      createUi({ clearLiveError: () => { errorClears2 += 1; } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller2.switchChatThread("chat-ok");
    await controller2.pollThreadLiveState();
    controller2.dispose();
    expect(errorClears2).toBeGreaterThanOrEqual(1);
  });

  test("acceptTaskPatch: calls client.acceptPatch with taskId; 409 is swallowed silently", async () => {
    const accepted: string[] = [];
    const infos: string[] = [];

    const happyBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [],
      }),
      acceptPatch: async (taskId) => {
        accepted.push(taskId);
        return {
          taskId, status: "SUCCEEDED", modifiedFiles: [], diagnostics: [], shadowWorkspacePath: null,
        } as TaskResult;
      },
    };

    const controller = new CrucibleController(
      () => happyBackend, new MemorySessionStore(), createSettings(),
      createUi({ showInfo: (m) => { infos.push(m); } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.acceptTaskPatch("task-accept");
    expect(accepted).toEqual(["task-accept"]);
    expect(infos.some((m) => m.includes("workspace"))).toBe(true);

    // 409 is benign and swallowed silently
    const errors: string[] = [];
    const conflictBackend: BackendTaskClient = {
      ...happyBackend,
      acceptPatch: async () => { throw Object.assign(new Error("conflict"), { status: 409 }); },
    };
    const controller2 = new CrucibleController(
      () => conflictBackend, new MemorySessionStore(), createSettings(),
      createUi({ showError: (m) => { errors.push(m); } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller2.acceptTaskPatch("task-409");
    expect(errors).toHaveLength(0); // swallowed
    controller.dispose();
    controller2.dispose();
  });

  // ── Tier B: lifecycle control + durable telemetry ─────────────────────────

  test("abortActiveTask posts {revert} for the live active task", async () => {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [], abortCalls: [],
      liveResponse: { activeTaskId: "task-run", status: "EXECUTING", pendingGates: [], plan: null, turnActive: false },
    };
    const backend = createStubBackend(state);
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-abort");
    await controller.pollThreadLiveState();      // sets latestLiveState.activeTaskId
    await controller.abortActiveTask(true);
    controller.dispose();
    expect(state.abortCalls).toEqual([{ taskId: "task-run", revert: true }]);
  });

  test("setReviewPref posts {autoAccept} for the live active task", async () => {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [], reviewPrefCalls: [],
      liveResponse: { activeTaskId: "task-run", status: "EXECUTING", pendingGates: [], plan: null, turnActive: false },
    };
    const backend = createStubBackend(state);
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-pref");
    await controller.pollThreadLiveState();
    await controller.setReviewPref(false);
    controller.dispose();
    expect(state.reviewPrefCalls).toEqual([{ taskId: "task-run", autoAccept: false }]);
  });

  test("setReviewPref reaches the backend with no thread or task open", async () => {
    // The backend keeps one value for every thread and background agent (spec §5.4).
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      reviewPrefCalls: [], globalReviewPrefCalls: [],
    };
    const backend = createStubBackend(state);
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.setReviewPref(true);
    controller.dispose();
    expect(state.globalReviewPrefCalls).toEqual([{ autoAccept: true }]);
    expect(state.reviewPrefCalls).toEqual([]);  // no task is running
  });

  test("setReviewPref reports edits it accepted for background agents", async () => {
    const infos: string[] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      setGlobalReviewPref: async () => ({ autoResolved: 1, background: 1 }),
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(),
      createUi({ showInfo: (m) => { infos.push(m); } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.setReviewPref(true);
    controller.dispose();
    expect(infos).toEqual(["Accepted 1 pending background edit."]);
  });

  test("onBackendReachable fires when the live poll recovers", async () => {
    let calls = 0;
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      getThreadLiveState: async () => {
        calls += 1;
        if (calls === 1) throw new Error("down");
        return { activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: false };
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    const reachable = vi.fn();
    controller.onBackendReachable(reachable);
    await controller.switchChatThread("chat-reach");
    await controller.pollThreadLiveState();
    await controller.pollThreadLiveState();
    controller.dispose();
    expect(reachable).toHaveBeenCalledTimes(1);
  });

  test("durable run_summary supersedes ephemeral counts in renderLiveReview", async () => {
    let reviewRendered: Parameters<ControllerUI["renderLiveReview"]>[0] | null = null;
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      getThreadLiveState: async () => ({
        activeTaskId: "t9",
        status: "READY_FOR_REVIEW",
        pendingGates: [],
        plan: null,
        turnActive: false,
        runSummary: { stepsCompleted: 3, stepsTotal: 4, deviations: ["1 delta replan(s)"] },
      }),
      getTaskResult: async () => ({
        taskId: "t9", status: "READY_FOR_REVIEW", modifiedFiles: ["a.py"], diagnostics: [],
        shadowWorkspacePath: "/shadow",
        plan: { analysis: "a", steps: [], expected_files: [], stop_conditions: [] },
        patch: { patch_ops: [] },
      } as TaskResult),
    };
    const ui = createUi({ renderLiveReview: (r) => { reviewRendered = r; } });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-rs");
    await controller.pollThreadLiveState();
    controller.dispose();
    // run_summary wins even though the plan has 0 steps and no steps were observed.
    expect(reviewRendered!.stepsCompleted).toBe(3);
    expect(reviewRendered!.stepsTotal).toBe(4);
    expect(reviewRendered!.deviations).toEqual(["1 delta replan(s)"]);
  });

  test("late-arriving task_narrative re-renders the ReviewCard (dedup must not lock it out)", async () => {
    // Smoke-found: the engine saves status=READY_FOR_REVIEW first, then synthesizes the
    // narrative (an LLM call) and saves it a moment later — so it arrives on a poll where
    // status is unchanged. The dedup signature must include it or the card never shows it.
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: { activeTaskId: "t9", status: "READY_FOR_REVIEW", pendingGates: [], plan: null, turnActive: false },
    };
    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      getThreadLiveState: async () => state.liveResponse!,
      getTaskResult: async () => ({
        taskId: "t9", status: "READY_FOR_REVIEW", modifiedFiles: ["a.py"], diagnostics: [],
        shadowWorkspacePath: "/s", plan: { analysis: "a", steps: [], expected_files: [], stop_conditions: [] },
        patch: { patch_ops: [] },
      } as TaskResult),
    };
    const reviews: Array<Parameters<ControllerUI["renderLiveReview"]>[0]> = [];
    const ui = createUi({ renderLiveReview: (r) => { reviews.push(r); } });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-late");
    await controller.pollThreadLiveState();   // poll 1: no narrative yet
    // narrative lands (status unchanged)
    state.liveResponse = {
      activeTaskId: "t9", status: "READY_FOR_REVIEW", pendingGates: [], plan: null,
      turnActive: false,
      taskNarrative: { outcome: "succeeded", headline: "Did X", points: ["a"] },
    };
    await controller.pollThreadLiveState();   // poll 2: must re-render with the narrative
    controller.dispose();
    expect(reviews.length).toBe(2);
    expect(reviews[0].narrative).toBeUndefined();
    expect(reviews[1].narrative?.headline).toBe("Did X");
  });

  test("task_narrative forwarded into renderLiveReview", async () => {
    let reviewRendered: Parameters<ControllerUI["renderLiveReview"]>[0] | null = null;
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      getThreadLiveState: async () => ({
        activeTaskId: "t9", status: "READY_FOR_REVIEW", pendingGates: [], plan: null,
        turnActive: false,
        taskNarrative: { outcome: "succeeded", headline: "Added refresh tokens", points: ["edited auth.py"] },
      }),
      getTaskResult: async () => ({
        taskId: "t9", status: "READY_FOR_REVIEW", modifiedFiles: ["auth.py"], diagnostics: [],
        shadowWorkspacePath: "/shadow",
        plan: { analysis: "a", steps: [], expected_files: [], stop_conditions: [] },
        patch: { patch_ops: [] },
      } as TaskResult),
    };
    const ui = createUi({ renderLiveReview: (r) => { reviewRendered = r; } });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-narr");
    await controller.pollThreadLiveState();
    controller.dispose();
    expect(reviewRendered!.narrative?.headline).toBe("Added refresh tokens");
    expect(reviewRendered!.narrative?.points).toEqual(["edited auth.py"]);
  });

  test("task_narrative forwarded into renderLiveError", async () => {
    let errorRendered: Parameters<ControllerUI["renderLiveError"]>[0] | null = null;
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      getThreadLiveState: async () => ({
        activeTaskId: "tf", status: "FAILED", pendingGates: [], plan: null,
        turnActive: false,
        taskNarrative: { outcome: "failed", headline: "Stopped at step 2", points: ["import broke"] },
      }),
    };
    const ui = createUi({ renderLiveError: (e) => { errorRendered = e; } });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-narr-fail");
    await controller.pollThreadLiveState();
    controller.dispose();
    expect(errorRendered!.narrative?.headline).toBe("Stopped at step 2");
  });

  test("durable failure_summary drives renderLiveError detail", async () => {
    let errorRendered: Parameters<ControllerUI["renderLiveError"]>[0] | null = null;
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      getThreadLiveState: async () => ({
        activeTaskId: "task-fail",
        status: "FAILED",
        pendingGates: [],
        plan: null,
        turnActive: false,
        failureSummary: { stepId: "s3", stepIndex: 3, errorClass: "VerifyPhaseExhausted", message: "boom" },
      }),
    };
    const ui = createUi({ renderLiveError: (e) => { errorRendered = e; } });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-fs");
    await controller.pollThreadLiveState();
    controller.dispose();
    expect(errorRendered!.detail).toContain("VerifyPhaseExhausted");
    expect(errorRendered!.detail).toContain("boom");
    expect(errorRendered!.detail).toContain("step 3");
  });

  test("rejectTaskPatch reports a true revert (not 'changes kept')", async () => {
    const infos: string[] = [];
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
    };
    const backend = createStubBackend(state);
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(),
      createUi({ showInfo: (m) => { infos.push(m); } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.rejectTaskPatch("task-x", "no thanks");
    controller.dispose();
    expect(state.rejectCalls).toEqual([{ taskId: "task-x", reason: "no thanks" }]);
    expect(infos.some((m) => m.toLowerCase().includes("discarded") || m.toLowerCase().includes("rolled back"))).toBe(true);
    expect(infos.some((m) => m.toLowerCase().includes("kept"))).toBe(false);
  });

  test("pollThreadLiveState re-entrancy guard: concurrent calls issue only one backend request", async () => {
    // A manual-release promise lets us hold the first in-flight call open while we
    // fire the second, so both are logically concurrent.
    let releaseFirstCall!: () => void;
    const firstCallHeld = new Promise<void>((resolve) => { releaseFirstCall = resolve; });
    let liveCallCount = 0;

    const slowBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [],
      }),
      getThreadLiveState: async (_threadId: string) => {
        liveCallCount += 1;
        await firstCallHeld; // block until released
        return NULL_LIVE_STATE;
      },
    };

    const controller = new CrucibleController(
      () => slowBackend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-reentrant");
    // Clear the call made by switchChatThread's immediate poke (it will also block).
    // Release it first so state is clean, then reset counter.
    releaseFirstCall();
    await Promise.resolve();
    liveCallCount = 0;

    // Re-arm the hold for the actual test.
    let release2!: () => void;
    const held2 = new Promise<void>((res) => { release2 = res; });
    let callCount2 = 0;
    const slowBackend2: BackendTaskClient = {
      ...slowBackend,
      getThreadLiveState: async (_threadId: string) => {
        callCount2 += 1;
        await held2;
        return NULL_LIVE_STATE;
      },
    };
    const controller2 = new CrucibleController(
      () => slowBackend2, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller2.switchChatThread("chat-reentrant-2");
    // After switchChatThread's poke is in-flight, fire a second concurrent call.
    // Neither has resolved yet.
    const p1 = controller2.pollThreadLiveState();
    const p2 = controller2.pollThreadLiveState();
    // Release the held call so both can finish.
    release2();
    await Promise.all([p1, p2]);
    // Only one backend call should have been issued despite two invocations.
    expect(callCount2).toBe(1);
    controller.dispose();
    controller2.dispose();
  });

  test("getTaskResult failure resets signature so next poll retries", async () => {
    let resultCallCount = 0;

    const retryBackend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      getThreadLiveState: async () => ({
        activeTaskId: "task-retry",
        status: "READY_FOR_REVIEW",
        pendingGates: [],
        plan: null,
        turnActive: false,
      }),
      getTaskResult: async () => {
        resultCallCount += 1;
        throw new Error("not ready");
      },
    };

    const controller = new CrucibleController(
      () => retryBackend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-retry");
    // First poll: getTaskResult throws → signature reset
    await controller.pollThreadLiveState();
    // Second poll: signature was reset so getTaskResult is called again
    await controller.pollThreadLiveState();
    controller.dispose();

    expect(resultCallCount).toBeGreaterThanOrEqual(2);
  });

  test("prompt files: lists and expands from the workspace", async () => {
    const ws = await fsp.mkdtemp(path.join(os.tmpdir(), "ctl-prompts-"));
    await fsp.mkdir(path.join(ws, ".crucible", "prompts"), { recursive: true });
    await fsp.writeFile(path.join(ws, ".crucible", "prompts", "review.md"), "Review $1", "utf8");

    const state: StubBackendState = {
      submitPayloads: [],
      getTaskCalls: [],
      acceptCalls: [],
      rejectCalls: [],
      getResultCalls: [],
      planFeedbackCalls: [],
    };
    const controller = new CrucibleController(
      () => createStubBackend(state),
      new MemorySessionStore(),
      createSettings(),
      createUi({ getWorkspacePath: () => ws }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-03-03T00:00:00.000Z"
    );

    expect(await controller.listPrompts()).toEqual(["review"]);
    expect(await controller.expandPrompt("review", "src/a.py")).toEqual({
      found: true,
      text: "Review src/a.py",
    });
    expect(await controller.expandPrompt("ghost", "")).toEqual({ found: false, text: "" });

    await fsp.rm(ws, { recursive: true, force: true });
  });
});

describe("CrucibleController.openChat — setup routing (first-run gap)", () => {
  const emptyState = (): StubBackendState => ({
    submitPayloads: [],
    getTaskCalls: [],
    acceptCalls: [],
    rejectCalls: [],
    getResultCalls: [],
    planFeedbackCalls: [],
  });

  function build(getConfig: () => Promise<{ provider?: unknown }>) {
    const backend = { ...createStubBackend(emptyState()), getConfig } as unknown as BackendTaskClient;
    const setupPrompts: string[] = [];
    let chatOpened = false;
    const ui = createUi({
      promptSetup: (msg: string) => setupPrompts.push(msg),
      openChatPanel: () => {
        chatOpened = true;
      },
    });
    const controller = new CrucibleController(
      () => backend,
      new MemorySessionStore(),
      createSettings(),
      ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-03-03T00:00:00.000Z",
    );
    return { controller, setupPrompts, chatOpened: () => chatOpened };
  }

  test("prompts setup (not a raw error) when no provider is configured", async () => {
    const h = build(async () => ({ provider: null }));
    await h.controller.openChat();
    expect(h.setupPrompts.length).toBe(1);
    expect(h.chatOpened()).toBe(false);
  });

  test("prompts setup when the backend is unreachable", async () => {
    const h = build(async () => {
      throw new Error("ECONNREFUSED");
    });
    await h.controller.openChat();
    expect(h.setupPrompts.length).toBe(1);
    expect(h.chatOpened()).toBe(false);
  });

  test("opens chat normally when a provider is configured", async () => {
    const h = build(async () => ({ provider: { backend: "turboquant", model: "qwen3" } }));
    await h.controller.openChat();
    expect(h.setupPrompts.length).toBe(0);
    expect(h.chatOpened()).toBe(true);
  });
});

describe("rewind", () => {
  const REWIND_RESULT = {
    restoredFiles: ["a.py"], deletedFiles: [], oversizeFiles: [], failed: [],
    removedMessages: 2, prefillText: "try again", retiredMemories: 1,
  };
  const THREAD = {
    threadId: "chat-1", workspacePath: "/ws", title: "t",
    messages: [{ role: "user", content: "kept", type: "text", timestamp: "", metadata: {} }],
    touchedFiles: [],
  };

  test("reloads the thread and prefills the composer after a rewind", async () => {
    const prefilled: string[] = [];
    const calls: string[][] = [];
    let threadFetched = false;
    const backend = {
      rewindThread: async (t: string, m: string) => { calls.push([t, m]); return REWIND_RESULT; },
      getChatThread: async () => { threadFetched = true; return THREAD; },
      listChatThreads: async () => [],
      getThreadLiveState: async () => ({ pendingGates: [] }),
    } as unknown as BackendTaskClient;
    const ui = createUi({ prefillComposer: (t: string) => prefilled.push(t) });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async () => {} }, () => "2026-08-16T00:00:00.000Z",
    );
    controller.activeThreadId = "chat-1";

    await controller.rewindTo("m1");
    controller.dispose();

    expect(calls).toEqual([["chat-1", "m1"]]);
    expect(threadFetched).toBe(true);
    expect(prefilled).toEqual(["try again"]);
  });

  test("surfaces a rewind failure instead of silently reloading", async () => {
    const errors: string[] = [];
    let threadFetched = false;
    const backend = {
      rewindThread: async () => { throw new Error("409 turn in flight"); },
      getChatThread: async () => { threadFetched = true; return THREAD; },
    } as unknown as BackendTaskClient;
    const ui = createUi({ showError: (m: string) => errors.push(m) });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async () => {} }, () => "2026-08-16T00:00:00.000Z",
    );
    controller.activeThreadId = "chat-1";

    await controller.rewindTo("m1");
    controller.dispose();

    expect(errors.length).toBe(1);
    expect(threadFetched).toBe(false);
  });
});

describe("CrucibleController — sub-agents", () => {
  const AGENT: AgentSummary = {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
    status: "running", now: "read_file a.py", toolCount: 2, filesChangedCount: 0,
    startedAt: "2026-10-01T00:00:00Z", endedAt: null, reportPreview: "",
  };

  function setup(extra: Record<string, unknown> = {}, uiExtra: Partial<ControllerUI> = {}) {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [], liveResponse: NULL_LIVE_STATE,
    };
    const listCalls: string[] = [];
    const backend = {
      ...createStubBackend(state),
      listAgents: async (threadId: string) => { listCalls.push(threadId); return [{ ...AGENT, status: "completed", toolCount: 9 }]; },
      ...extra,
    } as BackendTaskClient;
    const rosters: AgentSummary[][] = [];
    const appended: ChatMessage[] = [];
    const ui = createUi({
      renderAgents: (agents) => { rosters.push(agents); },
      appendChatMessage: (m) => { appended.push(m); },
      ...uiExtra,
    });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-10-01T00:00:00.000Z",
    );
    return { state, controller, rosters, appended, listCalls };
  }

  test("switching to a thread loads its roster from the routes", async () => {
    const { controller, rosters, listCalls } = setup();
    await controller.switchChatThread("chat-1");
    await Promise.resolve(); await Promise.resolve();
    controller.dispose();
    expect(listCalls).toEqual(["chat-1"]);
    expect(rosters.at(-1)?.[0]).toMatchObject({ agentId: "agent-a", toolCount: 9 });
  });

  test("/live agents render, are in the dedup signature, and turn end refreshes from the routes", async () => {
    const { state, controller, rosters, listCalls } = setup();
    await controller.switchChatThread("chat-1");
    await Promise.resolve();
    controller.dispose();
    rosters.length = 0; listCalls.length = 0;

    state.liveResponse = { ...NULL_LIVE_STATE, turnActive: true, agents: [AGENT] };
    await controller.pollThreadLiveState();
    expect(rosters).toHaveLength(1);
    await controller.pollThreadLiveState();
    expect(rosters).toHaveLength(1);  // unchanged → deduped
    state.liveResponse = { ...NULL_LIVE_STATE, turnActive: true, agents: [{ ...AGENT, toolCount: 3 }] };
    await controller.pollThreadLiveState();
    expect(rosters).toHaveLength(2);  // a row change re-renders: agents ARE in the signature

    state.liveResponse = NULL_LIVE_STATE;  // the turn ended
    await controller.pollThreadLiveState();
    await Promise.resolve(); await Promise.resolve();
    expect(listCalls).toEqual(["chat-1"]);
  });

  test("a live roster message is appended as a contract ChatMessage; agent events poke /live", async () => {
    const { state, controller, appended } = setup({
      sendChatMessage: asStream(async function* () {
        yield { type: "agent_dispatch" as const, payload: { message: {
          role: "agent", content: "", type: "agent_dispatch", task_id: null,
          timestamp: "2026-10-01T00:00:00Z", metadata: { agent_ids: ["agent-a"] } } } };
        yield { type: "agent_started" as const, payload: {
          agent_id: "agent-a", parent_agent_id: null, depth: 1, name: "explore", label: "survey" } };
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      }),
    });
    await controller.switchChatThread("chat-1");
    const before = state.liveCalls!.length;
    await controller.sendChatMessage("go");
    controller.dispose();
    const roster = appended.find((m) => m.type === "agent_dispatch");
    expect(roster).toMatchObject({ type: "agent_dispatch", taskId: null, metadata: { agent_ids: ["agent-a"] } });
    expect(state.liveCalls!.length).toBeGreaterThan(before);
  });

  test("stopAgent posts to the agent's stop route", async () => {
    const stops: Array<[string, string]> = [];
    const { controller } = setup({
      stopAgent: async (threadId: string, agentId: string) => { stops.push([threadId, agentId]); return { ok: true }; },
    });
    await controller.switchChatThread("chat-1");
    await controller.stopAgent("agent-a");
    controller.dispose();
    expect(stops).toEqual([["chat-1", "agent-a"]]);
  });
  test("team summaries load on open, /live teams are in the signature, and a team leaving /live refreshes", async () => {
    const TEAM = { teamId: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 1,
      maxRounds: 3, pausedReason: null, members: [], openProposals: [],
      usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z" };
    const LIVE = { teamId: "team-1", name: "auth", phase: "DELIBERATING", round: 1,
      maxRounds: 3, pausedReason: null, members: [] };
    const teamLists: string[] = [];
    const summaries: unknown[][] = [];
    const lives: unknown[][] = [];
    const { state, controller } = setup({
      listTeams: async (threadId: string) => { teamLists.push(threadId); return [TEAM]; },
    }, {
      renderTeams: (t) => { summaries.push(t); },
      renderLiveTeams: (t) => { lives.push(t); },
    });
    await controller.switchChatThread("chat-1");
    await Promise.resolve(); await Promise.resolve();
    controller.dispose();
    expect(teamLists).toEqual(["chat-1"]);
    expect(summaries).toHaveLength(1);

    state.liveResponse = { ...NULL_LIVE_STATE, teams: [LIVE] };
    await controller.pollThreadLiveState();
    await controller.pollThreadLiveState();
    expect(lives).toHaveLength(1);                       // unchanged → deduped
    state.liveResponse = { ...NULL_LIVE_STATE, teams: [{ ...LIVE, round: 2 }] };
    await controller.pollThreadLiveState();
    expect(lives).toHaveLength(2);                       // teams ARE in the signature

    teamLists.length = 0;
    state.liveResponse = NULL_LIVE_STATE;                // the team ended
    await controller.pollThreadLiveState();
    await Promise.resolve(); await Promise.resolve();
    expect(teamLists).toEqual(["chat-1"]);
  });
});

describe("CrucibleController — background agents (spec §5.3, §6)", () => {
  const baseState = (): StubBackendState => ({
    submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
    getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
  });
  const make = (backend: BackendTaskClient, ui: ControllerUI) => new CrucibleController(
    () => backend, new MemorySessionStore(), createSettings(), ui,
    { openDiff: async (_entry: ReviewFileEntry) => {} },
    () => "2026-06-11T00:00:00.000Z"
  );

  test("a queued send keeps the bubble, re-enables input and opens no stream", async () => {
    const appended: ChatMessage[] = [];
    const enabled: boolean[] = [];
    const removed: string[] = [];
    const queued: string[] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      sendChatMessage: async () => ({ kind: "queued", messageId: "m1" }),
    };
    const controller = make(backend, createUi({
      appendChatMessage: (m) => { appended.push(m); },
      setChatInputEnabled: (e) => { enabled.push(e); },
      removeChatMessage: (id) => { removed.push(id); },
      markQueued: (id) => { queued.push(id); },
    }));
    await controller.switchChatThread("t1");
    await controller.sendChatMessage("hi");
    controller.dispose();
    const bubble = appended.filter((m) => m.role === "user");
    expect(bubble).toHaveLength(1);
    expect(removed).toEqual([]);
    expect(enabled.at(-1)).toBe(true);
    // The bubble is tagged queued until the notice turn takes it (spec §5.3).
    expect(queued).toEqual([bubble[0].id]);
  });

  test("a failed send removes the bubble and restores the draft", async () => {
    const removed: string[] = [];
    const drafts: string[] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      sendChatMessage: async () => { throw new Error("Chat message failed (500)"); },
    };
    const controller = make(backend, createUi({
      removeChatMessage: (id) => { removed.push(id); },
      restoreDraft: (t) => { drafts.push(t); },
    }));
    await controller.switchChatThread("t1");
    await controller.sendChatMessage("hi");
    controller.dispose();
    expect(removed).toHaveLength(1);
    expect(drafts).toEqual(["hi"]);
  });

  test("reconciles the transcript when the backend has more messages, once", async () => {
    let fetches = 0;
    const replaced: ChatMessage[][] = [];
    const message = (content: string): ChatMessage =>
      ({ role: "agent", content, type: "text", timestamp: "2026-06-11T00:00:00Z", metadata: {} } as ChatMessage);
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      getChatThread: async (threadId: string) => {
        fetches += 1;
        return { threadId, workspacePath: "/w", title: "t", createdAt: "x",
                 messages: fetches === 1 ? [] : [message("a"), message("b")] } as never;
      },
      getThreadLiveState: async () => ({
        activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: false,
        agentsRunning: 0, turnKind: null, messageCount: 2 }),
    };
    const controller = make(backend, createUi({
      replaceChatMessages: (m) => { replaced.push(m); },
    }));
    await controller.switchChatThread("t1");        // fetch 1: an empty transcript
    await controller.pollThreadLiveState();
    await new Promise((r) => setTimeout(r, 0));
    await controller.pollThreadLiveState();
    controller.dispose();
    expect(replaced.map((m) => m.length)).toEqual([2]);
    expect(fetches).toBe(2);
  });

  test("a background agent's gate opens no thread relay", async () => {
    let streams = 0;
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      streamChannel: async function* () { streams += 1; },
      getThreadLiveState: async () => ({
        activeTaskId: null, status: null, plan: null, turnActive: false,
        agentsRunning: 1, turnKind: null, messageCount: 0,
        pendingGates: [{ gateId: "g", kind: "edit", payload: {},
                         agent: { id: "a", label: "a", name: "explore" } }] }),
    };
    const controller = make(backend, createUi());
    await controller.switchChatThread("t1");
    await controller.pollThreadLiveState();
    controller.dispose();
    expect(streams).toBe(0);
  });

  test("notifies once per attention change, never for the open thread", async () => {
    const seen: Array<[string, number]> = [];
    let rows = [{ threadId: "t2", pendingGates: 1, agentsRunning: 0 },
                { threadId: "t1", pendingGates: 3, agentsRunning: 0 }];
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      getAttention: async () => rows,
    };
    const controller = make(backend, createUi());
    await controller.switchChatThread("t1");
    controller.onAttention((id, n) => seen.push([id, n]));
    await controller.pollAttention();
    await controller.pollAttention();
    rows = [{ threadId: "t2", pendingGates: 2, agentsRunning: 0 }];
    await controller.pollAttention();
    controller.dispose();
    expect(seen).toEqual([["t2", 1], ["t2", 2]]);
  });
});
