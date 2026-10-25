import type { HistoryView } from "./history.js";
// Shared conversation view state and message projection. Both hosts supply a
// current-view port, so reload recovery and confirmation rules have one owner.
import type { ApprovalView, FirstSendChoice } from "./approval-mode.js";
import type {
  ProviderAccess, AgentDetail, AgentSummary, ChatMessage, ChatThreadSummary,
  RewindPreview, SequencedStreamEvent, TeamDetail, TeamLive, TeamSummary,
  ThreadUsage, ContextUsage,
} from "@crucible/editor-client";
import type { TeamViewEvent } from "./team-views.js";
import type { ChatAdmissionView, LiveGateView, LivePlanView, LiveSessionsView, LiveTodosView } from "./controller.js";
import type { ListModelsHandler } from "./chat-message-handlers.js";

export interface ChatViewPort {
  postMessage(message: Record<string, unknown>): void;
  isOpen(): boolean;
  identity(): unknown;
  createRequestId(): string;
  listModels: ListModelsHandler;
}

export class ChatView {
  private historyView: HistoryView | undefined;
  renderHistoryState(history: HistoryView): void {
    this.historyView = history;
    this.viewPort.postMessage({ type: "historyState", history });
  }
  private chatAdmission: ChatAdmissionView | null = null;

  renderChatAdmission(admission: ChatAdmissionView | null): void {
    this.chatAdmission = admission;
    this.viewPort.postMessage({ type: "chatAdmission", admission });
  }

  private approvalView: ApprovalView | null = null;

  constructor(private readonly viewPort: ChatViewPort) {}

  retryDecision(threadId: string, kind: "mode" | "clarify" | "team_plan", gateId?: string): void {
    void this.viewPort.postMessage({ type: "decisionRetry", threadId, kind, gateId });
  }

  // The first-send Full access question is asked by the React app, not a VS Code modal, so the
  // same dialog guards a standalone host. Pending questions survive a webview reload.
  private fullAccessQuestions = new Map<string, { workspace: string; resolve(choice: FirstSendChoice): void }>();
  confirmFullAccessFirstSend(workspace: string): Promise<FirstSendChoice> {
    if (!this.viewPort.isOpen()) return Promise.resolve("cancel");
    const requestId = this.viewPort.createRequestId();
    return new Promise(resolve => {
      this.fullAccessQuestions.set(requestId, { workspace, resolve });
      void this.viewPort.postMessage({ type: "confirmFullAccessFirstSend", requestId, workspace });
    });
  }
  answerFullAccessQuestion(requestId: unknown, choice: unknown): void {
    const question = typeof requestId === "string" ? this.fullAccessQuestions.get(requestId) : undefined;
    if (!question) return;
    this.fullAccessQuestions.delete(requestId as string);
    question.resolve(choice === "continue" || choice === "accept_edits" ? choice : "cancel");
  }

  renderApproval(view: ApprovalView): void {
    this.approvalView = view;
    void this.viewPort.postMessage({ type: "approvalState", approval: view });
  }

  /** Replay retained host state before preferences and conversation hydration. */
  replayState(): void {
    if (this.approvalView) this.renderApproval(this.approvalView);
    for (const [requestId, { workspace }] of this.fullAccessQuestions) {
      void this.viewPort.postMessage({ type: "confirmFullAccessFirstSend", requestId, workspace });
    }
    this.renderChatAdmission(this.chatAdmission);
    if (this.historyView) this.renderHistoryState(this.historyView);
    this.renderContextUsage(this.lastContextUsage, this.contextUsageStale);
    // Webview state resets on reload; the workbar is SSE-fed (not in the
    // /live poll), so replay the last posted value or step progress shows
    // a bare "Executing…" until the next step_started event.
    if (this.lastWorkbarInfo !== null) {
      this.updateWorkbar(this.lastWorkbarInfo);
    }
  }

  /** Closing a view cancels its unanswered first-send questions. */
  disposeViewState(): void {
    for (const { resolve } of this.fullAccessQuestions.values()) resolve("cancel");
    this.fullAccessQuestions.clear();
  }

  appendMessage(message: ChatMessage): void {
    this.viewPort.postMessage({ type: "appendMessage", message });
  }

  appendChunk(chunk: string): void {
    this.viewPort.postMessage({ type: "appendChunk", chunk });
  }

  showThinking(message: string): void {
    this.viewPort.postMessage({ type: "showThinking", message });
  }

  updateThinking(message: string): void {
    this.viewPort.postMessage({ type: "updateThinking", message });
  }

  hideThinking(): void {
    this.viewPort.postMessage({ type: "hideThinking" });
  }

  setInputEnabled(enabled: boolean): void {
    this.viewPort.postMessage({ type: "setInputEnabled", enabled });
  }

  renderThreadList(
    threads: ChatThreadSummary[],
    activeThreadId: string
  ): void {
    this.viewPort.postMessage({ type: "renderThreadList", threads, activeThreadId });
  }

  clearThread(threadId?: string): void {
    this.lastWorkbarInfo = null;
    this.viewPort.postMessage({ type: "clearThread", ...(threadId === undefined ? {} : { threadId }) });
  }

  showRewindPreview(preview: RewindPreview): void {
    this.viewPort.postMessage({ type: "rewindPreviewResult", preview });
  }

  prefillComposer(text: string, draft?: { threadId: string; imageAttachments: { name: string; mediaType: string; dataUrl: string; bytes: number }[] }): void {
    this.viewPort.postMessage({ type: "composerPrefill", text, ...draft });
  }

  resolveInlineChangeCard(taskId: string, resolution: "applied" | "discarded"): void {
    this.viewPort.postMessage({ type: "resolveInlineChangeCard", taskId, resolution });
  }

  updateThreadTitle(threadId: string, title: string): void {
    this.viewPort.postMessage({ type: "thread_title_updated", payload: { thread_id: threadId, title } });
  }

  appendThinkingEntry(text: string): void {
    this.viewPort.postMessage({ type: "appendThinkingEntry", text });
  }

  appendThinkingChunk(chunk: string): void {
    this.viewPort.postMessage({ type: "appendThinkingChunk", chunk });
  }

  finalizeAgentMessage(): void {
    this.viewPort.postMessage({ type: "finalizeAgentMessage" });
  }

  // Live, state-driven cards (Class A). The webview keeps a single slot per kind and
  // replaces in place, so these are safe to call every poll tick.
  // Every pending gate as a list (spec §4.5); an empty list clears the gate cards.
  renderLiveGates(gates: LiveGateView[]): void {
    this.viewPort.postMessage({ type: "renderLiveGates", gates });
  }

  clearLiveGates(): void {
    this.viewPort.postMessage({ type: "renderLiveGates", gates: [] });
  }

  renderLivePlan(plan: LivePlanView): void {
    this.viewPort.postMessage({ type: "renderLivePlan", plan });
  }

  clearLivePlan(): void {
    this.viewPort.postMessage({ type: "clearLivePlan" });
  }

  renderLiveReview(review: { taskId: string; modifiedFiles: string[]; shadowWorkspacePath: string | null; stepsCompleted: number | null; stepsTotal: number | null; deviations: string[] }): void {
    this.viewPort.postMessage({ type: "renderLiveReview", review });
  }

  clearLiveReview(): void {
    this.viewPort.postMessage({ type: "clearLiveReview" });
  }

  renderLiveError(error: { taskId: string; status: "FAILED" | "ABORTED"; detail?: string }): void {
    this.viewPort.postMessage({ type: "renderLiveError", error });
  }

  clearLiveError(): void {
    this.viewPort.postMessage({ type: "clearLiveError" });
  }

  renderLiveTodos(todos: LiveTodosView): void {
    this.viewPort.postMessage({ type: "renderLiveTodos", todos });
  }

  renderProviderAccess(access: ProviderAccess | null): void {
    this.viewPort.postMessage({ type: "renderProviderAccess", access });
  }

  clearLiveTodos(): void {
    this.viewPort.postMessage({ type: "clearLiveTodos" });
  }

  renderLiveSessions(sessions: LiveSessionsView): void {
    this.viewPort.postMessage({ type: "renderLiveSessions", sessions });
  }

  clearLiveSessions(): void {
    this.viewPort.postMessage({ type: "clearLiveSessions" });
  }

  sendLiveStatus(
    status: string | null, turnActive: boolean,
    extra?: { turnKind: "user" | "notice" | null; agentsRunning: number },
  ): void {
    this.viewPort.postMessage({
      type: "liveStatus", status, turnActive,
      turnKind: extra?.turnKind ?? null, agentsRunning: extra?.agentsRunning ?? 0,
    });
  }

  replaceMessages(messages: ChatMessage[]): void {
    this.viewPort.postMessage({ type: "replaceMessages", messages });
  }

  removeMessage(id: string): void {
    this.viewPort.postMessage({ type: "removeChatMessage", id });
  }

  markQueued(id: string): void {
    this.viewPort.postMessage({ type: "markQueued", id });
  }

  restoreDraft(text: string, recovery?: { draftId: string; mentionedPaths?: string[]; imageAttachments: { name: string; mediaType: string; dataUrl: string; bytes: number }[] }): void {
    this.viewPort.postMessage(recovery ? { type: "draftRecovery", text, ...recovery } : { type: "composerPrefill", text });
  }

  renderAgents(agents: AgentSummary[]): void {
    this.viewPort.postMessage({ type: "renderAgents", agents });
  }

  agentDetail(agentId: string, detail: AgentDetail): void {
    this.viewPort.postMessage({ type: "agentDetail", agentId, detail });
  }

  agentEvent(agentId: string, event: SequencedStreamEvent): void {
    this.viewPort.postMessage({ type: "agentEvent", agentId, event });
  }

  private modelRefreshEpoch = 0;

  async refreshModels(): Promise<void> {
    const identity = this.viewPort.identity();
    const epoch = ++this.modelRefreshEpoch;
    const result = await this.viewPort.listModels();
    if (epoch === this.modelRefreshEpoch && identity === this.viewPort.identity()) {
      this.viewPort.postMessage({ type: "modelList", ...result });
    }
  }

  private lastContextUsage: ContextUsage | null = null;
  private contextUsageStale = false;

  renderContextUsage(usage: ContextUsage | null, stale: boolean): void {
    this.lastContextUsage = usage;
    this.contextUsageStale = stale;
    this.viewPort.postMessage({ type: "renderContextUsage", usage, stale });
  }

  renderThreadUsage(usage: ThreadUsage | null): void {
    this.viewPort.postMessage({ type: "renderThreadUsage", usage });
  }

  renderTeams(teams: TeamSummary[]): void {
    this.viewPort.postMessage({ type: "renderTeams", teams });
  }

  renderLiveTeams(teams: TeamLive[]): void {
    this.viewPort.postMessage({ type: "renderLiveTeams", teams });
  }

  teamDetail(teamId: string, detail: TeamDetail): void {
    this.viewPort.postMessage({ type: "teamDetail", teamId, detail });
  }

  teamEvent(teamId: string, event: TeamViewEvent): void {
    this.viewPort.postMessage({ type: "teamEvent", teamId, event });
  }

  appendToolEvent(event: { id: number; tool: string; args: Record<string, unknown>; thought?: string; source: "explore" | "execution" | "planning" }): void {
    this.viewPort.postMessage({ type: "appendToolEvent", event });
  }

  appendToolResult(id: number, output: string, isError: boolean): void {
    this.viewPort.postMessage({ type: "appendToolResult", id, output, isError });
  }

  private lastWorkbarInfo: { stepIndex?: number; totalSteps?: number; stepTitle?: string; phaseLabel?: string } | null = null;

  updateWorkbar(info: { stepIndex?: number; totalSteps?: number; stepTitle?: string; phaseLabel?: string } | null): void {
    this.lastWorkbarInfo = info;
    this.viewPort.postMessage({ type: "updateWorkbar", info });
  }

  updateRetryStatus(status: { attempt: number; max_attempts: number; reason: string; message: string } | null): void {
    this.viewPort.postMessage({ type: "updateRetryStatus", status });
  }

  updateTokenProgress(
    progress: {
      thinking: number;
      output: number;
      input: number | null;
      exact: boolean;
    } | null,
  ): void {
    this.viewPort.postMessage({ type: "updateTokenProgress", progress });
  }

  updateEditFailure(failure: { reason: string; ops: number } | null): void {
    this.viewPort.postMessage({ type: "updateEditFailure", failure });
  }

}
