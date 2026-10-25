import { routeChatMessage } from "../src/chat-message-router.js";
import { ChatAdmissionUnknownError } from "@crucible/editor-client";
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
  const knownThreads = new Set<string>();
  return {
    getChatActivity: async workspacePath => ({ supported: true, version: 1, requestId: "fresh", requestedWorkspace: workspacePath,
      workspacePath, processId: "test-process", threads: [...knownThreads].map(threadId => ({ threadId, turnActive: false, agentsRunning: 0, sessionsRunning: 0 })) }),
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
      knownThreads.add(threadId); state.liveCalls?.push(threadId);
      return { ...(state.liveResponse ?? NULL_LIVE_STATE), processId: state.liveResponse?.processId ?? "test-process" };
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
    renderProviderAccess: () => {},
    renderLiveSessions: () => {},
    clearLiveSessions: () => {},
    sendLiveStatus: () => {},
    promptSetup: () => {},
    renderAgents: () => {},
    agentDetail: () => {},
    agentEvent: () => {},
    renderTeams: () => {},
    renderThreadUsage: () => {},
    renderContextUsage: () => {},
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
    const reportedErrors2: string[] = [];
    const controller2 = new CrucibleController(
      () => nonAbortBackend, new MemorySessionStore(), createSettings(),
      createUi({
        setChatInputEnabled: (enabled) => { finalEnabled2 = enabled; },
        showError: message => reportedErrors2.push(message),
      }),
      { openDiff: async () => {} },
      () => "2026-06-10T00:00:00.000Z"
    );
    await controller2.switchChatThread("chat-abort");
    for (let n = 0; n < 60; ++n) await Promise.resolve();
    await expect(controller2.sendChatMessage("boom")).resolves.toBeUndefined();
    expect(reportedErrors2).toEqual(["Stream error: backend exploded"]);
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
    // A controller command gate: no task, gate id is the thread.
    state.liveResponse = {
      activeTaskId: null, status: null, plan: null, turnActive: true,
      pendingGates: [{ gateId: "g-cmd", kind: "command", payload: { command: "rm", args: ["-rf", "x"] }, agent: null }],
    };
    await controller.pollThreadLiveState();  // sets latestLiveState (activeTaskId null)
    for (let n = 0; n < 60; ++n) await Promise.resolve();

    await controller.handleCommandDecisionFromChat("thread-9", {
      approve: true, remember: false, scope: "exact",
    }, "g-cmd");

    expect(chatSent).toEqual([{
      threadId: "thread-9",
      decision: { approve: true, remember: false, scope: "exact" },
      gateId: "g-cmd",
    }]);
    expect(taskSent).toEqual([]);  // NOT the task route
    controller.dispose();
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
    (controller as any).stopLiveStatePolling(); // stop timers; the controller is still live
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
    controller.dispose();
  });

  test("pollThreadLiveState delivers the provider access card and its clearing", async () => {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: NULL_LIVE_STATE,
    };
    const backend = createStubBackend(state);
    const renders: unknown[] = [];
    const ui = createUi({ renderProviderAccess: (access) => { renders.push(access); } });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-1");
    await Promise.resolve();
    (controller as any).stopLiveStatePolling(); // stop timers; the controller is still live
    renders.length = 0;

    const access = { kind: "usage_limit", message: "Usage limit reached.", code: null,
                     status: 429, requestId: null };
    // Nothing else changes: the card alone must defeat the dedup (signature invariant).
    state.liveResponse = { ...NULL_LIVE_STATE, providerAccess: access };
    await controller.pollThreadLiveState();
    expect(renders).toEqual([access]);
    state.liveResponse = NULL_LIVE_STATE;
    await controller.pollThreadLiveState();
    expect(renders).toEqual([access, null]);
    controller.dispose();
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
    (controller as any).stopLiveStatePolling(); // stop timers; the controller is still live
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
    controller.dispose();
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
    (controller as any).stopLiveStatePolling(); // stop timers; the controller is still live
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
    controller.dispose();
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
    (controller as any).stopLiveStatePolling(); // stop timers; the controller is still live
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
    controller.dispose();
  });

  test("live-resume: follows saved history without replaying the channel on reload", async () => {
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
    expect(streamChannelCalls).toBe(0);
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
    expect(errorClears).toBe(1); // selection clears stale actionable state before the fresh FAILED card

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

  test("snapshots originating previews before rewind and prefills native upload draft", async () => {
    let rewound = false;
    const prefill = vi.fn();
    const backend = {
      getChatThread: async () => ({ ...THREAD, messages: rewound ? [] : [{
        ...THREAD.messages[0], id: "m1", metadata: { image_previews: [{ name: "one.png", dataUrl: "data:image/png;base64,YQ==" }] },
      }] }),
      rewindThread: async () => { rewound = true; return REWIND_RESULT; },
      listChatThreads: async () => [],
      getThreadLiveState: async () => ({ pendingGates: [] }),
    } as unknown as BackendTaskClient;
    const controller = new CrucibleController(() => backend, new MemorySessionStore(), createSettings(),
      createUi({ prefillComposer: prefill }), { openDiff: async () => {} });
    controller.activeThreadId = "chat-1";
    await controller.rewindTo("m1");
    controller.dispose();
    expect(prefill).toHaveBeenCalledWith("try again", { threadId: "chat-1", imageAttachments: [{
      name: "one.png", mediaType: "image/png", dataUrl: "data:image/png;base64,YQ==", bytes: 1,
    }] });
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
    expect(threadFetched).toBe(true); // pre-rewind image snapshot, not a post-failure reload
  });
});

describe("CrucibleController — context capacity", () => {
  const THREAD = { threadId: "chat-1", workspacePath: "/ws", title: "t", messages: [], createdAt: "", updatedAt: "" } as any;
  const context = (revision = 1) => ({ thread_id: "chat-1", provider: "chatgpt", model: "gpt", scope: "main", process_id: "p", generation: 1, revision, turn_id: "t", request_id: "r", status: "in_flight", input_tokens: revision * 100, input_quality: "estimated", context_window: 1000, capacity_source: "chatgpt_catalog", observed_at: "2026-10-08T00:00:00Z" });
  function setup(extra: Record<string, unknown> = {}) {
    const state: StubBackendState = { submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [], getResultCalls: [], planFeedbackCalls: [], liveResponse: { ...NULL_LIVE_STATE, processId: "p", contextUsage: context() as any } };
    const rendered: Array<{ usage: any; stale: boolean }> = [];
    const controller = new CrucibleController(() => ({ ...createStubBackend(state), ...extra }), new MemorySessionStore(), createSettings(), createUi({ renderContextUsage: (usage: any, stale: boolean) => rendered.push({ usage, stale }) } as any), { openDiff: async () => {} });
    controller.activeThreadId = "chat-1";
    return { controller, state, rendered };
  }
  test("live context updates before unrelated signature dedup and unchanged recovery clears stale", async () => {
    let fails = false;
    const { controller, state, rendered } = setup({ getThreadLiveState: async () => { if (fails) throw new Error("offline"); return state.liveResponse; } });
    await controller.pollThreadLiveState();
    expect(rendered.at(-1)).toMatchObject({ usage: { input_tokens: 100 }, stale: false });
    state.liveResponse = { ...state.liveResponse!, contextUsage: context(2) as any };
    await controller.pollThreadLiveState();
    expect(rendered.at(-1)?.usage.input_tokens).toBe(200);
    fails = true; await controller.pollThreadLiveState();
    expect(rendered.at(-1)?.stale).toBe(true);
    fails = false; await controller.pollThreadLiveState();
    expect(rendered.at(-1)).toMatchObject({ usage: { input_tokens: 200, observed_at: context().observed_at }, stale: false });
    controller.dispose();
  });
  test("successful live polls without telemetry render stale context and valid recovery clears it", async () => {
    const { controller, state, rendered } = setup();
    await controller.pollThreadLiveState();
    state.liveResponse = { ...state.liveResponse!, contextUsage: null };
    await controller.pollThreadLiveState();
    expect(rendered.at(-1)).toMatchObject({ usage: { input_tokens: 100 }, stale: true });
    state.liveResponse = { ...state.liveResponse!, contextUsage: context() as any };
    await controller.pollThreadLiveState();
    expect(rendered.at(-1)).toMatchObject({ usage: { input_tokens: 100 }, stale: false });
    controller.dispose();
  });
  test("SSE context goes through validation/order and never reads output token progress", async () => {
    const { controller, rendered } = setup({ sendChatMessage: asStream(async function* () {
      yield { type: "context_usage", payload: context(3) } as any;
      yield { type: "context_usage", payload: context(2) } as any;
      yield { type: "context_usage", payload: { ...context(4), input_tokens: true } } as any;
      yield { type: "chat_done", payload: {} } as any;
    }) });
    await controller.pollThreadLiveState(); await controller.sendChatMessage("draft");
    expect(rendered.at(-1)?.usage.input_tokens).toBe(300);
    controller.dispose();
  });
  test("an EOF without chat_done retains a visibly stale observation", async () => {
    const { controller, rendered } = setup({ sendChatMessage: asStream(async function* () { yield { type: "context_usage", payload: context(3) } as any; }) });
    await controller.pollThreadLiveState(); await controller.sendChatMessage("draft");
    expect(rendered.at(-1)).toMatchObject({ usage: { input_tokens: 300 }, stale: true }); controller.dispose();
  });
  test("refresh preserves no-op measurement when hydration fails", async () => {
    let fail = false;
    const { controller, state, rendered } = setup({ getThreadLiveState: async () => { if (fail) throw new Error("offline"); return state.liveResponse; } });
    await controller.pollThreadLiveState(); fail = true;
    await controller.refreshContextUsage();
    expect(rendered.at(-1)).toMatchObject({ usage: { input_tokens: 100 }, stale: true });
    controller.dispose();
  });
  test("a send response arriving after A-B-A cannot start a new stream", async () => {
    let resolve!: (result: SendChatResult) => void;
    let consumed = false;
    const { controller, rendered } = setup({ sendChatMessage: async () => new Promise<SendChatResult>(r => { resolve = r; }) });
    await controller.pollThreadLiveState();
    const send = controller.sendChatMessage("old");
    await controller.switchChatThread("chat-2"); await controller.switchChatThread("chat-1");
    resolve({ kind: "stream", events: (async function* () { consumed = true; yield { type: "context_usage", payload: context(99) } as any; })() });
    await send;
    expect(consumed).toBe(false);
    expect(rendered.at(-1)?.usage?.revision).not.toBe(99); controller.dispose();
  });
  test("open transcript fetched before A-B-A is retired", async () => {
    let release!: (thread: ChatThread) => void; let first = true;
    const { controller } = setup({ getConfig: async () => ({ provider: "chatgpt" }), getChatThread: async () => { if (first) { first = false; return new Promise<ChatThread>(r => { release = r; }); } return THREAD; } });
    const clears = vi.spyOn((controller as any).ui, "clearChatThread");
    const opened = controller.openChat(); await new Promise(r => setTimeout(r, 0));
    await controller.switchChatThread("chat-2"); await controller.switchChatThread("chat-1");
    const count = clears.mock.calls.length; release(THREAD); await opened;
    expect(clears.mock.calls.length).toBe(count); controller.dispose();
  });
  test("new-thread create started before navigation is retired", async () => {
    let release!: (thread: ChatThread) => void;
    const { controller } = setup({ createChatThread: async () => new Promise<ChatThread>(r => { release = r; }) });
    const created = controller.newChatThread(); await controller.switchChatThread("chat-2");
    release(THREAD); await created; expect(controller.activeThreadId).toBe("chat-2"); controller.dispose();
  });
  test("context arriving before transcript survives clear and unchanged dedup", async () => {
    let finish!: (thread: ChatThread) => void;
    let first = true;
    const { controller } = setup({ getChatThread: async () => { if (first) { first = false; return new Promise<ChatThread>(r => { finish = r; }); } return THREAD; } });
    let view: any = null;
    (controller as any).ui.renderContextUsage = (usage: any) => { view = usage; };
    (controller as any).ui.clearChatThread = () => { view = null; };
    const switching = controller.switchChatThread("chat-1"); await controller.pollThreadLiveState();
    expect(view?.input_tokens).toBe(100); finish(THREAD); await switching;
    await controller.pollThreadLiveState(); expect(view?.input_tokens).toBe(100); controller.dispose();
  });
  test("transcript reconciliation started before A-B-A cannot replace current messages", async () => {
    let finish!: (thread: ChatThread) => void; let first = true;
    const { controller } = setup({ getChatThread: async () => { if (first) { first = false; return new Promise<ChatThread>(r => { finish = r; }); } return THREAD; } });
    const replaced = vi.spyOn((controller as any).ui, "replaceChatMessages");
    const reconcile = (controller as any).reconcileTranscript("chat-1", 3);
    await controller.switchChatThread("chat-2"); await controller.switchChatThread("chat-1"); finish(THREAD); await reconcile;
    expect(replaced).not.toHaveBeenCalled(); controller.dispose();
  });
  test("same-URL connection replacement retires pending send and poll", async () => {
    let finishPoll!: (live: ThreadLiveState) => void;
    let finishSend!: (result: SendChatResult) => void; let consumed = false; let first = true;
    const { controller, rendered } = setup({
      getThreadLiveState: async () => { if (first) { first = false; return new Promise<ThreadLiveState>(r => { finishPoll = r; }); } return { ...NULL_LIVE_STATE, processId: "new", contextUsage: { ...context(), process_id: "new" } }; },
      sendChatMessage: async () => new Promise<SendChatResult>(r => { finishSend = r; }),
    });
    const poll = controller.pollThreadLiveState(); const send = controller.sendChatMessage("old");
    await controller.reconnectContextUsage();
    finishPoll({ ...NULL_LIVE_STATE, processId: "p", contextUsage: context(99) as any }); await poll;
    finishSend({ kind: "stream", events: (async function* () { consumed = true; })() }); await send;
    expect(consumed).toBe(false); expect(rendered.at(-1)?.usage.process_id).toBe("new"); controller.dispose();
  });
  test("create-on-send response cannot select a thread after navigation", async () => {
    let finish!: (thread: ChatThread) => void; let sent = false;
    const { controller } = setup({ createChatThread: async () => new Promise<ChatThread>(r => { finish = r; }), sendChatMessage: async () => { sent = true; return { kind: "queued" }; } });
    controller.activeThreadId = null;
    const send = controller.sendChatMessage("old"); await controller.switchChatThread("chat-2");
    finish(THREAD); await send; expect(controller.activeThreadId).toBe("chat-2"); expect(sent).toBe(false); controller.dispose();
  });
  test("navigation retires a deferred old poll before it can populate the new view", async () => {
    let resolve!: (live: ThreadLiveState) => void;
    let first = true;
    const { controller, rendered } = setup({ getThreadLiveState: async () => { if (first) { first = false; return new Promise<ThreadLiveState>(r => { resolve = r; }); } return { ...NULL_LIVE_STATE, processId: "p", contextUsage: null }; } });
    const old = controller.pollThreadLiveState();
    await controller.switchChatThread("chat-2");
    resolve({ ...NULL_LIVE_STATE, processId: "p", contextUsage: context(99) as any }); await old;
    expect(rendered.at(-1)?.usage).toBeNull(); controller.dispose();
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
    (controller as any).stopLiveStatePolling(); // stop timers; the controller is still live
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
    controller.dispose();
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
    for (let n = 0; n < 60; ++n) await Promise.resolve();
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
    (controller as any).stopLiveStatePolling(); // stop timers; the controller is still live
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
    controller.dispose();
  });
  const U = (input: number) => ({ requests: 1, input, output: 1, cached: 0 });
  const usageOf = (input: number) => ({ total: U(input), main: U(input), agents: {}, teams: {} });

  test("usage loads on thread open, refreshes while busy, once on idle, and dedups", async () => {
    vi.useFakeTimers();
    try {
      let input = 100;
      const fetched: string[] = [];
      const rendered: unknown[] = [];
      const { state, controller } = setup({
        getThreadUsage: async (threadId: string) => { fetched.push(threadId); return usageOf(input); },
      }, { renderThreadUsage: (u) => { rendered.push(u); } });
      await controller.switchChatThread("chat-1");
      await vi.advanceTimersByTimeAsync(0);
      expect(fetched).toEqual(["chat-1"]);
      expect(rendered.at(-1)).toEqual(usageOf(100));

      state.liveResponse = { ...NULL_LIVE_STATE, turnActive: true };
      await controller.pollThreadLiveState();
      expect(fetched).toHaveLength(1);              // throttled: < 3s since the last
      vi.advanceTimersByTime(3000);
      input = 250;
      await controller.pollThreadLiveState();
      await vi.advanceTimersByTimeAsync(0);
      expect(fetched).toHaveLength(2);
      expect(rendered.at(-1)).toEqual(usageOf(250));

      const renders = rendered.length;
      state.liveResponse = NULL_LIVE_STATE;          // the turn ended: one final fetch
      await controller.pollThreadLiveState();
      await vi.advanceTimersByTimeAsync(0);
      expect(fetched).toHaveLength(3);
      expect(rendered).toHaveLength(renders);        // unchanged numbers → not re-sent
      vi.advanceTimersByTime(10000);
      await controller.pollThreadLiveState();
      expect(fetched).toHaveLength(3);               // idle: no polling

      await controller.switchChatThread("chat-1");   // a reopened webview starts empty
      await controller.pollThreadLiveState();         // the next tick of the running poll
      await vi.advanceTimersByTimeAsync(0);
      expect(fetched).toHaveLength(4);
      expect(rendered.at(-1)).toEqual(usageOf(250)); // re-sent though unchanged
      controller.dispose();
    } finally {
      vi.useRealTimers();
    }
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
    for (let n = 0; n < 60; ++n) await Promise.resolve();
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
    for (let n = 0; n < 60; ++n) await Promise.resolve();
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
    expect(replaced.map((m) => m.length)).toEqual([0, 2]);
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

describe("Phase 3 approval admission integration", () => {
  function fixture() {
    const state: StubBackendState = { submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [], getResultCalls: [], planFeedbackCalls: [] };
    const backend = createStubBackend(state);
    const ui = createUi({ retryDecision: vi.fn(), restoreDraft: vi.fn(), removeChatMessage: vi.fn(), showError: vi.fn() });
    const c = new CrucibleController(() => backend, new MemorySessionStore(), createSettings(), ui, { openDiff: async () => {} });
    (c as unknown as { activeThreadId: string }).activeThreadId = "t";
    c.configureApproval({ savedMode: () => "ask", reviewOn: () => true, saveMode: async () => {}, warningDismissed: () => false, saveWarningDismissed: async () => {},
      isTrusted: () => true, firstSendAcknowledged: () => true, acknowledgeFirstSend: async () => {},
      confirmFirstFullAccessSend: async () => "continue",
      sandboxEnabled: () => true, saveSandboxEnabled: async () => {}, notifySandboxUnavailable: () => {} });
    return { c, backend, ui };
  }
  const snapshot = { supported: true as const, autoAccept: false, shellPolicy: "ask" as const, version: 1, owned: true, processId: "process" };
  test.each(["message", "mode", "clarify", "team"] as const)("%s waits for readiness and includes process identity", async kind => {
    const { c, backend } = fixture();
    let resolve!: (value: unknown) => void;
    backend.getConfig = vi.fn(() => new Promise(r => { resolve = r; })) as BackendTaskClient["getConfig"];
    backend.sendChatMessage = vi.fn(async () => ({ kind: "queued", messageId: "m" }));
    backend.postModeDecision = vi.fn(async function* () {});
    backend.postClarifyDecision = vi.fn(async function* () {});
    backend.decideTeamPlan = vi.fn(async () => ({})) as BackendTaskClient["decideTeamPlan"];
    const pending = kind === "message" ? c.sendChatMessage("hi") : kind === "mode" ? c.handleModeDecisionFromChat("t", "edit") : kind === "clarify" ? c.handleClarifyDecisionFromChat("t", "yes") : c.decideTeamPlan("t", "g", "approve");
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    const method = kind === "message" ? backend.sendChatMessage : kind === "mode" ? backend.postModeDecision : kind === "clarify" ? backend.postClarifyDecision : backend.decideTeamPlan;
    expect(method).not.toHaveBeenCalled();
    resolve({ approval: snapshot, chatControllerEnabled: true }); await pending;
    if (kind === "message") expect(method).toHaveBeenCalledWith("t", "hi", expect.any(AbortSignal), expect.objectContaining({ expectedProcessId: "process", onAdmitted: expect.any(Function) }));
    else if (kind === "mode") expect(method).toHaveBeenCalledWith("t", "edit", "process", expect.objectContaining({ signal: expect.any(AbortSignal), onAdmitted: expect.any(Function) }));
    else if (kind === "clarify") expect(method).toHaveBeenCalledWith("t", "yes", "process", expect.objectContaining({ signal: expect.any(AbortSignal), onAdmitted: expect.any(Function) }));
    else expect(method).toHaveBeenCalledWith("t", "g", "approve", undefined, "process");
    c.dispose();
  });
  test.each(["message", "mode", "clarify", "team"] as const)("%s recovers structured rejection and retains input or card", async kind => {
    const { c, backend, ui } = fixture();
    backend.getConfig = vi.fn(async () => ({ approval: snapshot, chatControllerEnabled: true })) as BackendTaskClient["getConfig"];
    const error = { status: 409, detail: { code: "approval_not_confirmed", process_id: "new" } };
    backend.sendChatMessage = vi.fn(async () => { throw error; });
    backend.postModeDecision = vi.fn(async function* () { throw error; });
    backend.postClarifyDecision = vi.fn(async function* () { throw error; });
    backend.decideTeamPlan = vi.fn(async () => { throw error; });
    if (kind === "message") { await c.sendChatMessage("hi"); expect(ui.restoreDraft).toHaveBeenCalledWith("hi", undefined); expect(ui.removeChatMessage).toHaveBeenCalled(); }
    else if (kind === "mode") { await c.handleModeDecisionFromChat("t", "edit"); expect(ui.retryDecision).toHaveBeenCalledWith("t", "mode"); }
    else if (kind === "clarify") { await c.handleClarifyDecisionFromChat("t", "yes"); expect(ui.retryDecision).toHaveBeenCalledWith("t", "clarify"); }
    else { await c.decideTeamPlan("t", "g", "approve"); expect(ui.retryDecision).toHaveBeenCalledWith("t", "team_plan", "g"); }
    expect(backend.getConfig).toHaveBeenCalledTimes(2); expect(ui.showError).not.toHaveBeenCalled(); c.dispose();
  });
  test("a cancelled first Full access send keeps the draft and never reaches the backend", async () => {
    const { c, backend, ui } = fixture();
    const confirm = vi.fn(async () => "cancel" as const);
    c.configureApproval({ savedMode: () => "full_access", reviewOn: () => false, saveMode: async () => {}, warningDismissed: () => true,
      saveWarningDismissed: async () => {}, isTrusted: () => true, firstSendAcknowledged: () => false,
      acknowledgeFirstSend: async () => {}, confirmFirstFullAccessSend: confirm,
      sandboxEnabled: () => true, saveSandboxEnabled: async () => {}, notifySandboxUnavailable: () => {} });
    backend.getConfig = vi.fn(async () => ({ approval: { ...snapshot, autoAccept: true, shellPolicy: "allow_all" }, chatControllerEnabled: true })) as BackendTaskClient["getConfig"];
    backend.sendChatMessage = vi.fn();
    await c.sendChatMessage("hi");
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(backend.sendChatMessage).not.toHaveBeenCalled();
    expect(ui.restoreDraft).toHaveBeenCalledWith("hi", undefined);
    c.dispose();
  });
});


describe("CrucibleController — selected thread identity", () => {
  const thread = (threadId: string) => ({ threadId, workspacePath: "/tmp/workspace", title: `Title for ${threadId}`,
    messages: [{ role: "agent" as const, type: "text", content: `Message for ${threadId}`, timestamp: "" }], touchedFiles: [] });
  function fixture() {
    const state: StubBackendState = { submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [], getResultCalls: [], planFeedbackCalls: [] };
    const backend = createStubBackend(state);
    backend.getChatThread = vi.fn(async id => thread(id));
    backend.listChatThreads = vi.fn(async () => ["first", "second"].map(id => ({
      threadId: id, workspacePath: "/tmp/workspace", title: `Title for ${id}`, createdAt: "", agentsRunning: 0,
    })));
    backend.getConfig = vi.fn(async () => ({ provider: { backend: "scripted" } })) as BackendTaskClient["getConfig"];
    const clear = vi.fn(); const append = vi.fn(); const replace = vi.fn();
    const c = new CrucibleController(() => backend, new MemorySessionStore(), createSettings(),
      createUi({ clearChatThread: clear, appendChatMessage: append, replaceChatMessages: replace }), { openDiff: async () => {} });
    return { c, backend, clear, append, replace };
  }
  test("opening, switching and creating threads send the loaded thread identity with the transcript clear", async () => {
    const { c, backend, clear } = fixture();
    try {
      await c.openChat();
      expect(clear).toHaveBeenLastCalledWith("first");
      await c.switchChatThread("second");
      expect(clear).toHaveBeenLastCalledWith("second");
      await c.switchChatThread("first");
      expect(clear).toHaveBeenLastCalledWith("first");
      expect(backend.listChatThreads).toHaveBeenCalledTimes(1);
      await c.newChatThread();
      expect(clear).toHaveBeenLastCalledWith("chat-stub");
    } finally { c.dispose(); }
  });
  test("a delayed older switch cannot overwrite the newer selected identity or transcript", async () => {
    vi.useFakeTimers();
    const { c, backend, clear, append, replace } = fixture();
    let finish!: (value: ReturnType<typeof thread>) => void;
    backend.getChatThread = vi.fn(id => id === "first"
      ? new Promise<ReturnType<typeof thread>>(resolve => { finish = resolve; })
      : Promise.resolve(thread(id)));
    try {
      const older = c.switchChatThread("first");
      await c.switchChatThread("second");
      finish(thread("first")); await older;
      await vi.advanceTimersByTimeAsync(4100);
      expect(clear.mock.calls).toEqual([["first"], ["second"]]);
      expect(append).not.toHaveBeenCalled();
      expect(replace).toHaveBeenLastCalledWith(thread("second").messages);
      expect(c.activeThreadId).toBe("second");
    } finally { c.dispose(); vi.useRealTimers(); }
  });
});

describe("desktop draft lifecycle hooks", () => {
  const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: Error) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
  function fixture() {
    const state: StubBackendState = { submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [], getResultCalls: [], planFeedbackCalls: [] };
    const backend = createStubBackend(state);
    backend.sendChatMessage = vi.fn(async () => ({ kind: "queued", messageId: "m" }));
    const ui = createUi({ staleDraft: vi.fn(), restoreDraft: vi.fn(), draftAccepted: vi.fn(), draftCreated: vi.fn() });
    const c = new CrucibleController(() => backend, new MemorySessionStore(), createSettings(), ui, { openDiff: async () => {} });
    c.configureApproval({ savedMode: () => "ask", reviewOn: () => true, saveMode: async () => {}, warningDismissed: () => false, saveWarningDismissed: async () => {}, isTrusted: () => false,
      firstSendAcknowledged: () => false, acknowledgeFirstSend: async () => {}, confirmFirstFullAccessSend: async () => "cancel" });
    const seams = c as unknown as { activeThreadId: string | null; approvalReady(): Promise<string | undefined>; approval: { confirmFirstSend(): Promise<boolean> } };
    seams.activeThreadId = "origin";
    const ready = vi.spyOn(seams, "approvalReady").mockResolvedValue("process");
    const confirm = vi.spyOn(seams.approval, "confirmFirstSend").mockResolvedValue(true);
    const send = () => c.sendChatMessage("expanded", undefined, undefined, ["sample.txt"], undefined, [], "draft-id", "original");
    return { c, backend, ui, seams, ready, confirm, send };
  }
  test.each(["approval-error", "cancel", "continue", "create-ok", "create-error", "send-error"] as const)("stale %s returns the origin draft id without touching the visible composer", async phase => {
    const f = fixture(); const gate = deferred<unknown>();
    if (phase === "approval-error") f.ready.mockImplementation(() => gate.promise as Promise<string>);
    else if (phase === "cancel" || phase === "continue") f.confirm.mockImplementation(() => gate.promise as Promise<boolean>);
    else if (phase.startsWith("create")) { f.seams.activeThreadId = null; f.backend.createChatThread = vi.fn(() => gate.promise as ReturnType<BackendTaskClient["createChatThread"]>); }
    else f.backend.sendChatMessage = vi.fn(() => gate.promise as ReturnType<BackendTaskClient["sendChatMessage"]>);
    const pending = f.send();
    await vi.waitFor(() => expect(phase === "approval-error" ? f.ready : phase === "cancel" || phase === "continue" ? f.confirm : phase.startsWith("create") ? f.backend.createChatThread : f.backend.sendChatMessage).toHaveBeenCalled());
    await f.c.switchChatThread("visible");
    if (phase.endsWith("error")) gate.reject(new Error("admission refused"));
    else if (phase === "create-ok") gate.resolve({ threadId: "created", workspacePath: "/tmp/workspace", title: "New", createdAt: "", agentsRunning: 0 });
    else gate.resolve(phase === "continue");
    await pending;
    expect(f.ui.staleDraft).toHaveBeenCalledWith("original", { draftId: "draft-id", imageAttachments: [], mentionedPaths: ["sample.txt"] });
    expect(f.ui.restoreDraft).not.toHaveBeenCalled(); expect(f.ui.draftAccepted).not.toHaveBeenCalled();
    if (phase === "create-ok") expect(f.ui.draftCreated).toHaveBeenCalledWith("draft-id", "created");
    f.c.dispose();
  });
  test("acknowledges actual backend admission, even after navigation, instead of restoring a duplicate", async () => {
    const f = fixture(); const gate = deferred<SendChatResult>();
    f.backend.sendChatMessage = vi.fn(() => gate.promise);
    const pending = f.send(); await vi.waitFor(() => expect(f.backend.sendChatMessage).toHaveBeenCalled());
    expect(f.ui.draftAccepted).not.toHaveBeenCalled(); await f.c.switchChatThread("visible");
    gate.resolve({ kind: "queued", messageId: "m" }); await pending;
    expect(f.ui.draftAccepted).toHaveBeenCalledWith("draft-id", "origin"); expect(f.ui.staleDraft).not.toHaveBeenCalled(); f.c.dispose();
  });
  test("Cancel and current admission errors retain the existing restore path", async () => {
    const f = fixture(); f.confirm.mockResolvedValue(false); await f.send(); expect(f.backend.sendChatMessage).not.toHaveBeenCalled();
    expect(f.ui.restoreDraft).toHaveBeenCalledWith("original", expect.objectContaining({ draftId: "draft-id" }));
    f.confirm.mockResolvedValue(true); f.ready.mockRejectedValue(new Error("Approval timed out")); await f.send();
    expect(f.ui.restoreDraft).toHaveBeenCalledTimes(2); f.c.dispose();
  });
});


describe("CP6a0 operation attachment ownership", () => {
  function setup() {
    const state: StubBackendState = { submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [], getResultCalls: [], planFeedbackCalls: [], liveResponse: NULL_LIVE_STATE };
    const backend = createStubBackend(state);
    const ui = createUi({ renderChatAdmission: vi.fn(), showError: vi.fn(), showWarning: vi.fn(), showInfo: vi.fn(), restoreDraft: vi.fn(), staleDraft: vi.fn(), draftAccepted: vi.fn(),
      appendChatChunk: vi.fn(), setChatInputEnabled: vi.fn(), finalizeAgentMessage: vi.fn(), appendChatMessage: vi.fn() });
    const c = new CrucibleController(() => backend, new MemorySessionStore(), createSettings(), ui, { openDiff: async () => {} });
    c.activeThreadId = "A";
    return { c, backend, ui, state };
  }
  function deferred<T>() { let resolve!: (v: T) => void; let reject!: (e: unknown) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

  test("A→B→A retains pending admission and cancels only after accepted headers", async () => {
    const f = setup(); const response = deferred<SendChatResult>(); let signal!: AbortSignal; let admitted!: () => void;
    f.backend.sendChatMessage = vi.fn(async (_t, _text, s, options) => { signal = s!; admitted = options!.onAdmitted!; return response.promise; });
    const subscribe = vi.fn(async function* () {}); f.backend.streamChannel = subscribe;
    const send = f.c.sendChatMessage("original", undefined, undefined, undefined, undefined, undefined, "draft");
    await vi.waitFor(() => expect(signal).toBeDefined());
    await f.c.switchChatThread("B"); await f.c.switchChatThread("A");
    f.state.liveResponse = { ...NULL_LIVE_STATE, turnActive: true };
    await f.c.pollThreadLiveState(); expect(subscribe).not.toHaveBeenCalled(); expect(signal.aborted).toBe(false);
    admitted(); expect(signal.aborted).toBe(true);
    let iterated = false;
    response.resolve({ kind: "stream", events: (async function* () { iterated = true; })() }); await send;
    expect(iterated).toBe(false); expect(f.ui.draftAccepted).toHaveBeenCalledExactlyOnceWith("draft", "A");
    expect(f.ui.restoreDraft).not.toHaveBeenCalled(); f.c.dispose();
  });

  test("late old stream rejection and cleanup cannot alter B", async () => {
    const f = setup(); const blocked = deferred<void>();
    f.backend.sendChatMessage = async () => ({ kind: "stream", events: (async function* () { await blocked.promise; throw new Error("late A failure"); })() });
    const send = f.c.sendChatMessage("hi"); await new Promise(resolve => setTimeout(resolve, 0));
    await f.c.switchChatThread("B");
    const calls = (f.ui.setChatInputEnabled as ReturnType<typeof vi.fn>).mock.calls.length;
    blocked.reject(new Error("old socket failed")); await expect(send).resolves.toBeUndefined();
    expect(f.ui.showError).not.toHaveBeenCalled(); expect((f.ui.setChatInputEnabled as ReturnType<typeof vi.fn>).mock.calls.length).toBe(calls); f.c.dispose();
  });

  test("unknown receipt pins the original payload; only exact message ID confirms it", async () => {
    const f = setup(); let originalId = "";
    f.backend.sendChatMessage = vi.fn(async (_t, _m, _s, options) => { originalId = options!.messageId!; throw new ChatAdmissionUnknownError(new Error("lost response")); });
    const image = { name: "one.png", mediaType: "image/png", dataUrl: "data:image/png;base64,AA==", bytes: 1 };
    await f.c.sendChatMessage("hi", true, ["skill"], ["missing.txt"], false, [image], "draft", "original");
    expect(f.ui.restoreDraft).not.toHaveBeenCalled(); expect(f.ui.draftAccepted).not.toHaveBeenCalled();
    const owners = (f.c as any).pendingAdmissions as Set<any>;
    expect([...owners][0].unknown.submission).toEqual(["hi", true, ["skill"], ["missing.txt"], false, [image], "draft", "original"]);
    expect(f.ui.showWarning).toHaveBeenCalledWith(expect.stringContaining("unknown"));
    const thread = { threadId: "A", workspacePath: "/tmp/workspace", title: "A", createdAt: "", messages: [{ role: "user", type: "text", content: "hi", timestamp: "", id: "different" }] };
    f.backend.getChatThread = vi.fn(async () => thread as any);
    await f.c.checkChatReceipt(); expect(owners.size).toBe(1); expect(f.ui.draftAccepted).not.toHaveBeenCalled();
    await f.c.sendChatMessage("duplicate"); expect(f.backend.sendChatMessage).toHaveBeenCalledTimes(1);
    thread.messages[0]!.id = originalId;
    await f.c.checkChatReceipt(); expect(owners.size).toBe(0); expect(f.ui.draftAccepted).toHaveBeenCalledExactlyOnceWith("draft", "A");
    expect(f.backend.sendChatMessage).toHaveBeenCalledTimes(1); f.c.dispose();
  });

  test("accepted headers followed by broken queued confirmation never restore/retry the draft", async () => {
    const f = setup();
    f.backend.sendChatMessage = async (_t, _m, _s, options) => { options!.onAdmitted!(); throw new SyntaxError("bad 202 body"); };
    await f.c.sendChatMessage("hi", undefined, undefined, undefined, undefined, undefined, "draft");
    expect(f.ui.draftAccepted).toHaveBeenCalledExactlyOnceWith("draft", "A"); expect(f.ui.restoreDraft).not.toHaveBeenCalled();
    expect(f.ui.showWarning).toHaveBeenCalledWith(expect.stringContaining("accepted")); f.c.dispose();
  });

  test.each(["mode", "clarify"] as const)("%s header callback retires the pending origin without contaminating B", async kind => {
    const f = setup(); const gate = deferred<void>(); let signal!: AbortSignal; let admitted!: () => void;
    const decision = vi.fn(async function* (_t: string, _value: string, _pid?: string, options?: { signal?: AbortSignal; onAdmitted?: () => void }) {
      signal = options!.signal!; admitted = options!.onAdmitted!; await gate.promise; admitted(); yield { type: "chat_done", payload: {} } as StreamEvent;
    });
    if (kind === "mode") f.backend.postModeDecision = decision; else f.backend.postClarifyDecision = decision;
    const sending = kind === "mode" ? f.c.handleModeDecisionFromChat("A", "edit") : f.c.handleClarifyDecisionFromChat("A", "yes");
    await vi.waitFor(() => expect(signal).toBeDefined()); await f.c.switchChatThread("B"); expect(signal.aborted).toBe(false);
    const count = (f.ui.finalizeAgentMessage as ReturnType<typeof vi.fn>).mock.calls.length;
    gate.resolve(); await sending; expect(signal.aborted).toBe(true);
    expect((f.ui.finalizeAgentMessage as ReturnType<typeof vi.fn>).mock.calls.length).toBe(count); expect(f.ui.showError).not.toHaveBeenCalled(); f.c.dispose();
  });

  test("an old failed transcript fetch does not reach generic router recovery", async () => {
    const f = setup(); const fetch = deferred<any>();
    const normal = f.backend.getChatThread;
    f.backend.getChatThread = id => id === "old" ? fetch.promise : normal(id);
    const old = f.c.switchChatThread("old"); await f.c.switchChatThread("B");
    fetch.reject(new Error("late fetch")); await expect(old).resolves.toBeUndefined(); expect(f.ui.showError).not.toHaveBeenCalled(); f.c.dispose();
  });
  test.each(["mode", "clarify"] as const)("%s ambiguous dispatch keeps its origin reservation and never retries", async kind => {
    const f = setup();
    const decision = vi.fn(async function* () { throw new ChatAdmissionUnknownError(new Error("lost headers")); });
    if (kind === "mode") f.backend.postModeDecision = decision; else f.backend.postClarifyDecision = decision;
    if (kind === "mode") await f.c.handleModeDecisionFromChat("A", "edit"); else await f.c.handleClarifyDecisionFromChat("A", "yes");
    expect((f.c as any).pendingAdmissions.size).toBe(1);
    expect([...((f.c as any).pendingAdmissions as Set<any>)][0].unknown.submission).toEqual([kind, kind === "mode" ? "edit" : "yes"]);
    expect(f.ui.setChatInputEnabled).toHaveBeenLastCalledWith(false);
    await f.c.switchChatThread("B"); await f.c.switchChatThread("A"); await f.c.pollThreadLiveState();
    await f.c.sendChatMessage("new work"); expect(decision).toHaveBeenCalledTimes(1); expect((f.c as any).pendingAdmissions.size).toBe(1); f.c.dispose();
  });

  test("late Stop confirmation does not write a breadcrumb into another attachment", async () => {
    const f = setup(); const stopped = deferred<{ ok: boolean }>();
    f.backend.stopChatTurn = vi.fn(() => stopped.promise);
    const stop = f.c.stopActiveTurn(); await f.c.switchChatThread("B");
    const count = (f.ui.appendChatMessage as ReturnType<typeof vi.fn>).mock.calls.length;
    stopped.resolve({ ok: true }); await stop;
    expect(f.backend.stopChatTurn).toHaveBeenCalledExactlyOnceWith("A");
    expect((f.ui.appendChatMessage as ReturnType<typeof vi.fn>).mock.calls.length).toBe(count); f.c.dispose();
  });

  test("outer router boundary suppresses an A failure when navigation happens between rejection microtasks", async () => {
    const f = setup();
    f.backend.sendChatMessage = async () => ({ kind: "stream", events: (async function* () {
      queueMicrotask(() => queueMicrotask(() => { void f.c.switchChatThread("B"); }));
      throw new Error("old A stream");
    })() });
    const postMessage = vi.fn(); const showError = vi.fn();
    await routeChatMessage({ type: "sendMessage", text: "hi" }, {
      onMessage: (...args: Parameters<CrucibleController["sendChatMessage"]>) => f.c.sendChatMessage(...args), onGetPlanMode: () => false,
    } as any, { postMessage, showError } as any);
    expect(f.c.activeThreadId).toBe("B"); expect(postMessage).not.toHaveBeenCalled();
    expect(showError).not.toHaveBeenCalled(); expect(f.ui.showError).not.toHaveBeenCalled(); f.c.dispose();
  });

  test("current stream errors remain visible without generic router recovery", async () => {
    const f = setup(); f.backend.sendChatMessage = async () => ({ kind: "stream", events: (async function* () { throw new Error("current failure"); })() });
    await f.c.sendChatMessage("hi"); expect(f.ui.showError).toHaveBeenCalledWith("Stream error: current failure"); f.c.dispose();
  });

  test("receipt checks coalesce and abort at four seconds without treating absence as rejection", async () => {
    const f = setup();
    f.backend.sendChatMessage = async () => { throw new ChatAdmissionUnknownError("lost"); };
    await f.c.sendChatMessage("hi");
    let signal!: AbortSignal;
    f.backend.getChatThread = vi.fn((_id, s) => { signal = s!; return new Promise((_yes, no) => s!.addEventListener("abort", () => no(new Error("aborted")), { once: true })); });
    vi.useFakeTimers();
    try {
      const one = f.c.checkChatReceipt(); const two = f.c.checkChatReceipt(); expect(one).toBe(two);
      await vi.advanceTimersByTimeAsync(4001); await one;
      expect(signal.aborted).toBe(true); expect(f.backend.getChatThread).toHaveBeenCalledTimes(1);
      expect((f.c as any).pendingAdmissions.size).toBe(1); expect(f.ui.restoreDraft).not.toHaveBeenCalled();
    } finally { vi.useRealTimers(); f.c.dispose(); }
  });

  test.each(["pending", "unknown"] as const)("disposal clears %s origin payloads and suppresses late acknowledgements", async phase => {
    const f = setup(); const reply = deferred<SendChatResult>(); let admitted!: () => void;
    // Capture the callback without invoking it.
    f.backend.sendChatMessage = async (_t, _m, _s, options) => { admitted = options!.onAdmitted!; if (phase === "unknown") throw new ChatAdmissionUnknownError("lost"); return reply.promise; };
    const sending = f.c.sendChatMessage("hi", undefined, undefined, undefined, undefined, undefined, "draft");
    await vi.waitFor(() => expect(admitted).toBeDefined()); if (phase === "unknown") await sending;
    f.c.dispose(); expect((f.c as any).pendingAdmissions.size).toBe(0);
    admitted(); reply.resolve({ kind: "queued", messageId: "m" }); await sending;
    expect(f.ui.draftAccepted).not.toHaveBeenCalled();
  });

  test("Check receipt performs only a bounded origin-live read for an uncertain decision, never infers acceptance", async () => {
    const f = setup(); const decide = vi.fn(async function* () { throw new ChatAdmissionUnknownError("lost"); });
    f.backend.postClarifyDecision = decide; await f.c.handleClarifyDecisionFromChat("A", "yes");
    f.backend.getThreadLiveState = vi.fn(async () => NULL_LIVE_STATE);
    await f.c.checkChatReceipt();
    expect(f.backend.getThreadLiveState).toHaveBeenCalledExactlyOnceWith("A", expect.any(AbortSignal));
    expect((f.c as any).pendingAdmissions.size).toBe(1); expect(decide).toHaveBeenCalledTimes(1);
    expect(f.ui.showInfo).toHaveBeenCalledWith(expect.stringContaining("cannot confirm")); f.c.dispose();
  });

});
