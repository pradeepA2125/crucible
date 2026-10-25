import { WorkAuthority } from "./work-start.js";
import type { GateAction } from "./gate-actions.js";
import { gateDecisionIdentity, type GateDecisionClaim, type GateDecisionIdentity, type GateDecisionReceipt } from "@crucible/editor-client";
import { HistoryCache, HistoryReader, type HistoryView } from "./history.js";
import { ApprovalCoordinator, isApprovalMode, type ApprovalDependencies, type ApprovalView } from "./approval-mode.js";
import type {
  ProviderAccess,
  AgentDetail,
  AgentSummary,
  BackendTaskClient,
  ChatMessage,
  ChatThreadSummary,
  CommandDecision,
  McpToolDecision,
  StreamEvent,
  ResumeTaskResponse,
  SessionSummary,
  SessionTranscript,
  SequencedStreamEvent,
  TaskResult,
  TeamDetail,
  TeamLive,
  TeamSummary,
  TaskStatus,
  TaskSubmission,
  TaskView,
  ThreadLiveState,
  ThreadUsage,
  RewindPreview,
  SendChatResult,
  ThreadAttention,
} from "@crucible/editor-client";
import { ContextUsageState } from "./context-usage-state.js";
import type { ContextUsage } from "@crucible/editor-client";
import { ChatAdmissionUnknownError, parseWireChatMessage } from "@crucible/editor-client";
import { AgentViewManager } from "./agent-views.js";
import { TeamViewManager, type TeamViewEvent } from "./team-views.js";

const USAGE_REFRESH_MS = 3000;

export type ChatAdmissionView = { threadId: string; state: "pending" | "unknown"; checking: boolean };

type TurnOperation = {
  readonly threadId: string;
  readonly url: string;
  readonly epoch: number;
  readonly abort: AbortController;
  admitted: boolean;
  retired: boolean;
  unknown?: {
    client: BackendTaskClient;
    messageId?: string;
    accepted?: () => void | Promise<void>;
    gateIdentity?: GateDecisionIdentity;
    // Pin the complete origin submission, including images, independently of the UI.
    submission?: readonly unknown[];
    checking: boolean;
  };
};

import * as path from "path";
import type { MemoryDataSource } from "./memory-data.js";
import { listPromptNames, loadPromptBody, substitutePrompt } from "./prompt-files.js";
import { randomUUID } from "crypto";
import { readMentionedFiles } from "./mentioned-files.js";
import { buildReviewFileEntries } from "./review-files.js";
import { SessionStore } from "./session-store.js";
import { shouldStopPolling, TaskPoller } from "./task-poller.js";
import type {
  ReviewFileEntry,
  TaskMode,
  TaskSessionState,
} from "./types.js";

const TERMINAL_STATUSES: ReadonlySet<TaskStatus> = new Set(["SUCCEEDED", "FAILED", "ABORTED"]);

export interface SettingsProvider {
  getBackendBaseUrl(): string;
  getDefaultMode(): TaskMode;
  getPollIntervalMs(): number;
}

export interface ScopeDecisionPromptInput {
  files: string[];
  reason: string;
  stepId: string;
}

export interface ScopeDecisionPromptResult {
  decision: "approve" | "reject";
  remember: boolean;
}

export interface ControllerUI {
  getWorkspacePath(): string | null;
  promptForGoal(): Promise<string | undefined>;
  promptForTaskId(): Promise<string | undefined>;
  promptForRejectReason(): Promise<string | undefined>;
  promptForResumeStage(): Promise<"plan" | "feedback" | "execute" | undefined>;
  promptForMaxIterationsOverride(): Promise<number | undefined>;
  promptForScopeDecision(input: ScopeDecisionPromptInput): Promise<ScopeDecisionPromptResult | undefined>;
  showInfo(message: string): void;
  showWarning(message: string): void;
  showError(message: string): void;
  // Guide a first-run user into the setup wizard (unconfigured provider / unreachable
  // backend) instead of surfacing a raw error. The host renders an actionable prompt.
  promptSetup(message: string): void;
  openChatPanel(): void;
  appendChatMessage(message: ChatMessage): void;
  appendChatChunk(chunk: string): void;
  showChatThinking(message: string): void;
  updateChatThinking(message: string): void;
  hideChatThinking(): void;
  setChatInputEnabled(enabled: boolean): void;
  renderChatAdmission?(admission: ChatAdmissionView | null): void;
  renderHistoryState?(history: HistoryView): void;
  renderChatThreadList(threads: ChatThreadSummary[], activeThreadId: string): void;
  clearChatThread(threadId?: string): void;
  // Chat rewind: the preview feeds the confirm dialog, the prefill hands the rewound
  // message's text back to the composer for edit-or-resend.
  showRewindPreview(preview: RewindPreview): void;
  prefillComposer(text: string, draft?: { threadId: string; imageAttachments: { name: string; mediaType: string; dataUrl: string; bytes: number }[] }): void;
  resolveInlineChangeCard(taskId: string, resolution: "applied" | "discarded"): void;
  updateThreadTitle(threadId: string, title: string): void;
  appendChatThinkingEntry(text: string): void;
  appendChatThinkingChunk(chunk: string): void;
  finalizeAgentMessage(): void;
  // Live, state-driven cards (Class A). One slot per kind, replace-not-append; written
  // by both the SSE path (instant) and the /live poll (durable across reload/resume).
  renderLiveGates(gates: LiveGateView[]): void;
  clearLiveGates(): void;
  renderLivePlan(plan: LivePlanView): void;
  clearLivePlan(): void;
  appendToolEvent(event: { id: number; tool: string; args: Record<string, unknown>; thought?: string; source: "explore" | "execution" | "planning" }): void;
  appendToolResult(id: number, output: string, isError: boolean, approvalNote?: string, sandbox?: "blocked" | "outside"): void;
  updateWorkbar(info: { stepIndex?: number; totalSteps?: number; stepTitle?: string; phaseLabel?: string } | null): void;
  updateRetryStatus(status: { attempt: number; max_attempts: number; reason: string; message: string } | null): void;
  updateTokenProgress(
    progress: {
      thinking: number;
      output: number;
      input: number | null;
      exact: boolean;
    } | null,
  ): void;
  updateEditFailure(failure: { reason: string; ops: number } | null): void;
  renderLiveReview(review: { taskId: string; modifiedFiles: string[]; shadowWorkspacePath: string | null; stepsCompleted: number | null; stepsTotal: number | null; deviations: string[]; narrative?: { headline: string; points: string[] } }): void;
  clearLiveReview(): void;
  renderLiveError(error: { taskId: string; status: "FAILED" | "ABORTED"; detail?: string; narrative?: { headline: string; points: string[] } }): void;
  clearLiveError(): void;
  renderLiveTodos(todos: LiveTodosView): void;
  clearLiveTodos(): void;
  // Why the last turn stopped on the provider's account rules (ChatGPT plan usage
  // limit, ended sign-in…); null clears the card.
  renderProviderAccess(access: ProviderAccess | null): void;
  renderLiveSessions(sessions: LiveSessionsView): void;
  clearLiveSessions(): void;
  sendLiveStatus(
    status: string | null, turnActive: boolean,
    extra?: { turnKind: "user" | "notice" | null; agentsRunning: number },
  ): void;
  // Transcript reconcile and the queued-send fallbacks (spec §5.3, §6).
  replaceChatMessages(messages: ChatMessage[]): void;
  removeChatMessage(id: string): void;
  markQueued(id: string): void;
  // Optional desktop retention hooks: default hosts keep their composer behavior.
  draftCreated?(draftId: string, threadId: string): void;
  draftAccepted?(draftId: string, threadId: string): void;
  staleDraft?: ControllerUI["restoreDraft"];
  restoreDraft(text: string, recovery?: { draftId: string; mentionedPaths?: string[]; imageAttachments: { name: string; mediaType: string; dataUrl: string; bytes: number }[] }): void;
  // Sub-agents (spec §10): roster rows, and the open agents' transcripts and live events.
  renderAgents(agents: AgentSummary[]): void;
  agentDetail(agentId: string, detail: AgentDetail): void;
  agentEvent(agentId: string, event: SequencedStreamEvent): void;
  // Agent teams (spec v2 §9): summaries, live slow fields, and the open teams' boards.
  renderTeams(teams: TeamSummary[]): void;
  renderLiveTeams(teams: TeamLive[]): void;
  // Token usage for the composer total, team cards/boards and agent tabs. Null clears.
  renderThreadUsage(usage: ThreadUsage | null): void;
  renderContextUsage(usage: ContextUsage | null, stale: boolean): void;
  renderApproval?(view: ApprovalView): void;
  retryDecision?(threadId: string, kind: "mode" | "clarify" | "team_plan", gateId?: string): void;
  teamDetail(teamId: string, detail: TeamDetail): void;
  teamEvent(teamId: string, event: TeamViewEvent): void;
}

export interface LiveGateView {
  gateId: string;
  kind: "command" | "step" | "scope" | "validation" | "mode" | "edit" | "clarify" | "mcp_tool"
    | "team_plan";
  payload: Record<string, unknown>;
  // The active task for task gates ("task:" ids); the thread for controller gates.
  taskId: string;
  // The sub-agent that raised the gate; null for the main agent.
  agent: { id: string; label: string; name: string } | null;
  // The team whose card it is (spec v2 §8.5); null for every other gate.
  team: { id: string; name: string } | null;
}

export interface LivePlanView {
  taskId: string;
  planMarkdown: string;
}

export interface LiveTodosView {
  items: {
    title: string;
    status: "pending" | "in_progress" | "done" | "blocked" | "cancelled";
    note: string;
  }[];
}

export interface LiveSessionsView {
  items: SessionSummary[];
}

export interface DiffService {
  openDiff(entry: ReviewFileEntry): Promise<void>;
}

export type BackendClientFactory = (baseUrl: string) => BackendTaskClient;

export class CrucibleController {
  private session: TaskSessionState | null = null;
  private latestTask: TaskView | null = null;
  private latestResult: TaskResult | null = null;
  private poller: TaskPoller | null = null;
  private streamController: AbortController | null = null;
  private activeThreadId: string | null = null;
  // /live poll: re-derives the active gate/plan from persisted task state so cards
  // survive reload and resume task-id churn. Source of truth for action binding (Task 8).
  private liveStateTimer: ReturnType<typeof setInterval> | null = null;
  private latestLiveState: ThreadLiveState | null = null;
  private approval: ApprovalCoordinator | null = null;
  private lastLiveSignature: string | null = null;
  private backendReachableListeners: Array<() => void> = [];
  private livePollFailing = false;
  private readonly agentViews = new AgentViewManager(() => this.clientForChat(), {
    detail: (agentId, detail) => this.ui.agentDetail(agentId, detail),
    event: (agentId, event) => this.ui.agentEvent(agentId, event),
  });
  private readonly teamViews = new TeamViewManager(() => this.clientForChat(), {
    detail: (teamId, detail) => this.ui.teamDetail(teamId, detail),
    event: (teamId, event) => this.ui.teamEvent(teamId, event),
  });
  // Live team ids from the last changed /live: one that drops out has ended (spec §9).
  private lastLiveTeamIds: ReadonlySet<string> = new Set();
  // The previous poll's turnActive: a true→false edge is when /live stops reporting the
  // turn's agents, so the final roster comes from the routes then.
  private lastTurnActive = false;
  // Token usage changes on every model call, so it has its own route and cadence rather
  // than riding /live (whose dedup signature would churn): fetched on thread open, every
  // USAGE_REFRESH_MS while anything runs, and once more when everything stops.
  private usageThreadId: string | null = null;
  private usageFetchedAt = 0;
  private usageWasBusy = false;
  private lastUsageSignature: string | null = null;
  // Set only from a getChatThread result (spec §6): the transcript length the webview has.
  private lastReconciledCount = 0;
  private reconcilePending = false;
  // (threadId -> pendingGates) last notified, so a waiting gate does not re-notify.
  private readonly attentionNotified = new Map<string, number>();
  private readonly attentionListeners: Array<(threadId: string, pendingGates: number) => void> = [];
  // Structured tool event forwarding state
  private toolEventSeq = 0;
  private openToolEvent: Partial<Record<"explore" | "execution" | "planning", number>> = {};
  private lastStepStarted: { stepId: string; stepTitle: string; stepIndex: number; totalSteps: number } | null = null;
  private lastPatchError: string | null = null;
  private runDeviations: string[] = [];
  private deviationsTaskId: string | null = null;
  private disposed = false;
  private receiptCheck: Promise<void> | null = null;
  private turnOperation: TurnOperation | null = null;
  private readonly pendingAdmissions = new Set<TurnOperation>();
  private readonly historyCache = new HistoryCache();
  private readonly historyReader = new HistoryReader();
  private historyTimer: ReturnType<typeof setInterval> | null = null;
  private historyEpoch = -1;
  private historyLoaded = false;
  private readonly workAuthority = new WorkAuthority();
  private workReservation: { thread: string; url: string; epoch: number; verify(): Promise<void> } | null = null;
  private workBlocked = "Checking project activity…";
  private async runProjectWork<T>(thread: string, kind: string, gateId: string | undefined, action: (verifyWork: () => Promise<void>, isCurrentWork: () => boolean) => Promise<T>): Promise<T | undefined> {
    // Non-chat legacy callers retain their compatibility path; both UI hosts attach history before actions.
    if (this.historyEpoch < 0) return action(async () => {}, () => !this.disposed);
    const origin = this.contextView(), url = this.settings.getBackendBaseUrl(), workspace = this.ui.getWorkspacePath() ?? "", process = this.historyProcess;
    const continuation = !["message", "mode", "clarify", "feedback", "task"].includes(kind);
    if (this.workReservation || this.pendingAdmissions.size || !this.historyActionReady() || !process || thread !== this.activeThreadId) {
      this.ui.showWarning("Wait for current project activity and pending requests before starting work."); return;
    }
    const token = { thread, url, epoch: origin, verify: async () => {} }; this.workReservation = token;
    const current = () => !this.disposed && this.contextState.current(origin, this.settings.getBackendBaseUrl()) && this.activeThreadId === thread && process === this.historyProcess && this.ui.getWorkspacePath() === workspace;
    token.verify = async () => {
      const client = this.createClient(url), started = Date.now();
      const activity = await this.workAuthority.read(JSON.stringify([url, workspace]), client, workspace);
      if (!current() || !this.historyActionReady() || Date.now() - started >= 4000 || !activity.supported || activity.processId !== process
          || activity.requestedWorkspace !== workspace || !activity.threads.some(t => t.threadId === thread)) throw new Error("Project activity changed. Refresh before continuing.");
      const busy = activity.threads.filter(t => t.turnActive || t.agentsRunning || t.sessionsRunning);
      if (busy.some(t => t.threadId !== thread || !continuation && !(kind === "message" && this.latestLiveState?.turnKind === "notice"))) throw new Error("Another turn, agent or command is still running in this project. You can browse and edit drafts while it finishes.");
      if (continuation) {
        const live = await this.workAuthority.readGate(client, thread);
        if (!current() || !this.historyActionReady() || live.processId !== process || !gateId
            || !live.pendingGates.some(g => g.gateId === gateId && g.kind === kind)) throw new Error("This approval changed. Reopen the current card.");
      }
      if (!current()) throw new Error("The conversation changed before dispatch.");
    };
    try {
      await token.verify();
      if (!current()) throw new Error("The project or conversation changed before dispatch.");
      return await action(token.verify, current);
    } catch (error) {
      if (current()) { this.ui.showWarning(formatError(error)); if (["mode", "clarify", "feedback", "team_plan"].includes(kind)) this.ui.retryDecision?.(thread, kind === "feedback" ? "mode" : kind as "mode" | "clarify" | "team_plan", gateId); }
      return;
    } finally { if (this.workReservation === token) this.workReservation = null; }
  }
  private holdUnknownContinuation(error: unknown, kind: string, gateId: string): boolean {
    const reservation = this.workReservation;
    const status = (error as { status?: number } | null)?.status;
    if (!reservation || status !== undefined && status >= 400 && status < 500) return false;
    const owner: TurnOperation = { threadId: reservation.thread, url: reservation.url, epoch: reservation.epoch,
      abort: new AbortController(), admitted: false, retired: this.contextState.epoch !== reservation.epoch,
      unknown: { client: this.createClient(reservation.url), submission: [kind, gateId], checking: false } };
    this.pendingAdmissions.add(owner); this.publishAdmission();
    this.ui.showWarning("Approval receipt is unknown. Do not repeat it or start other project work. Check receipt for current status."); return true;
  }
  private async refreshWorkStatus(thread: string, epoch: number): Promise<void> {
    const url = this.settings.getBackendBaseUrl(), workspace = this.ui.getWorkspacePath() ?? "", process = this.historyProcess;
    try {
      const activity = await this.workAuthority.read(JSON.stringify([url, workspace]), this.createClient(url), workspace);
      if (this.disposed || this.activeThreadId !== thread || !this.contextState.current(epoch, this.settings.getBackendBaseUrl()) || process !== this.historyProcess) return;
      this.workBlocked = !activity.supported || activity.processId !== process ? "Project activity is unavailable. Reconnect before sending."
        : activity.threads.some(t => (t.turnActive || t.agentsRunning || t.sessionsRunning) && !(t.threadId === thread && this.latestLiveState?.turnKind === "notice")) ? "Work is running in this project. You can browse and edit drafts." : "";
    } catch { if (this.activeThreadId === thread && this.contextState.current(epoch, this.settings.getBackendBaseUrl()) && process === this.historyProcess) this.workBlocked = "Project activity is unavailable. Reconnect before sending."; }
    this.publishHistory();
  }

  private historySaved = false;
  private historyFailed = false;
  private historyLiveAt = 0;
  private historyNextAt = 0;
  private historyFailures = 0;
  private historyMutation = 0;
  private historyReadOwner = 0;
  private historyProcess: string | null = null;
  private historyNotice: string | undefined;
  private historyProjection = "";
  private readonly historyEchoes = new Map<string, Set<string>>();

  private historyKey(threadId: string): string {
    return HistoryCache.key(this.settings.getBackendBaseUrl(), this.ui.getWorkspacePath() ?? "", threadId);
  }
  private clearActionableHistory(): void {
    this.latestLiveState = null;
    this.lastLiveSignature = null;
    this.ui.clearLiveGates(); this.ui.clearLivePlan(); this.ui.clearLiveReview();
    this.ui.clearLiveError(); this.ui.clearLiveTodos(); this.ui.clearLiveSessions();
    this.ui.renderProviderAccess(null);
  }
  private historyActionReady(): boolean {
    return this.historyEpoch !== this.contextState.epoch || this.historyLoaded && !this.historyFailed
      && this.historyLiveAt > 0 && Date.now() - this.historyLiveAt < 5000;
  }
  private publishHistory(): void {
    if (this.disposed || !this.activeThreadId || this.historyEpoch !== this.contextState.epoch) return;
    const fresh = this.historyLoaded && this.historyLiveAt > 0 && Date.now() - this.historyLiveAt < 5000;
    const value: HistoryView = { threadId: this.activeThreadId,
      phase: fresh && !this.historyFailed ? "current" : this.historyFailed ? "unavailable" : "loading",
      navigationSafe: true, workBlocked: this.workBlocked, cached: this.historySaved, ...(this.historyNotice ? { notice: this.historyNotice } : {}) };
    const signature = JSON.stringify(value);
    if (signature !== this.historyProjection) { this.historyProjection = signature; this.ui.renderHistoryState?.(value); }
  }
  private attachHistory(threadId: string): void {
    this.historyReader.cancel();
    this.historyEpoch = this.contextState.epoch;
    this.historyLoaded = false; this.historyFailed = false; this.historyLiveAt = 0; this.historyProcess = null; this.workBlocked = "Checking project activity…";
    this.historyNextAt = 0; this.historyFailures = 0; this.historyProjection = ""; this.historyNotice = undefined;
    this.clearActionableHistory();
    this.ui.clearChatThread(threadId);
    const cached = this.historyCache.get(this.historyKey(threadId));
    this.historySaved = cached !== undefined;
    if (cached) this.ui.replaceChatMessages(cached);
    this.publishHistory();
    if (!this.historyTimer) this.historyTimer = setInterval(() => {
      if (this.disposed || !this.activeThreadId) return;
      if (this.historyLiveAt && Date.now() - this.historyLiveAt >= 3000) void this.pollThreadLiveState();
      if (this.historyLiveAt && Date.now() - this.historyLiveAt >= 5000) {
        this.historyLiveAt = 0; this.historyFailed = true; this.clearActionableHistory(); this.publishHistory();
      }
      void this.reconcileTranscript(this.activeThreadId, this.lastReconciledCount);
    }, 1000);
  }
  private get turnAbort(): AbortController | null { return this.turnOperation?.abort ?? null; }

  private beginTurnOperation(threadId: string, pending: boolean): TurnOperation {
    this.historyMutation++; this.historyReader.cancel();
    const owner: TurnOperation = { threadId, url: this.settings.getBackendBaseUrl(),
      epoch: this.contextView(), abort: new AbortController(), admitted: !pending, retired: false };
    this.turnOperation = owner;
    if (pending) this.pendingAdmissions.add(owner);
    this.publishAdmission();
    return owner;
  }

  private admitTurn(owner: TurnOperation, release = true): void {
    if (this.disposed) { owner.abort.abort(); return; }
    owner.admitted = true;
    if (this.workReservation?.thread === owner.threadId) this.workReservation = null;
    if (release) this.pendingAdmissions.delete(owner);
    this.publishAdmission();
    if (owner.retired) owner.abort.abort();
  }

  private finishTurn(owner: TurnOperation): void {
    if (owner.unknown) return;
    this.pendingAdmissions.delete(owner);
    owner.abort.abort();
    if (this.turnOperation === owner) this.turnOperation = null;
    if (!this.disposed && owner.admitted && owner.threadId === this.activeThreadId && owner.url === this.settings.getBackendBaseUrl()) {
      this.historyLoaded = false; this.historyLiveAt = 0; this.historyNextAt = 0; this.reconcilePending = true; this.publishHistory();
      void this.pollThreadLiveState();
    }
    this.publishAdmission();
  }

  private pendingFor(threadId: string, url: string): boolean {
    return [...this.pendingAdmissions].some(owner => owner.threadId === threadId && owner.url === url);
  }

  private publishAdmission(): void {
    if (this.disposed) return;
    const owner = this.pendingAdmissions.values().next().value as TurnOperation | undefined;
    this.ui.renderChatAdmission?.(owner ? { threadId: owner.threadId,
      state: owner.unknown ? "unknown" : "pending", checking: !!owner.unknown?.checking } : null);
  }

  /** Explicit, coalesced GET-only verification. Never infer rejection from absence. */
  checkChatReceipt(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (this.receiptCheck) return this.receiptCheck;
    this.receiptCheck = this.reconcileAdmissions().finally(() => { this.receiptCheck = null; });
    return this.receiptCheck;
  }

  private async reconcileAdmissions(): Promise<void> {
    for (const owner of this.pendingAdmissions) {
      const unknown = owner.unknown;
      if (!unknown || unknown.checking || owner.url !== this.settings.getBackendBaseUrl()) continue;
      unknown.checking = true;
      this.publishAdmission();
      const readAbort = new AbortController();
      const onDispose = () => readAbort.abort();
      owner.abort.signal.addEventListener("abort", onDispose, { once: true });
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        if (unknown.gateIdentity) {
          if (!unknown.client.getGateDecisionReceipt) continue;
          const identity = unknown.gateIdentity;
          const reading = unknown.client.getGateDecisionReceipt(identity, readAbort.signal);
          void reading.finally(() => { unknown.checking = false; this.publishAdmission(); }).catch(() => {});
          const deadline = new Promise<never>((_resolve, reject) => {
            timer = setTimeout(() => { readAbort.abort(); reject(new Error("Receipt check timed out")); }, 4000);
          });
          const result = await Promise.race([reading, deadline]);
          if (this.disposed || owner.unknown !== unknown || !this.pendingAdmissions.has(owner)) continue;
          if (!result.found || !this.gateReceiptMatches(identity, result.receipt)) continue;
          await unknown.accepted?.();
          delete owner.unknown; this.admitTurn(owner); this.finishTurn(owner);
          if (result.receipt.status === "launch_failed") this.ui.showWarning("The decision was accepted, but its work could not start. It has not been resent.");
          continue;
        }
        const reading = unknown.messageId
          ? this.readSavedHistory(owner.threadId, unknown.client).then(messages => ({ messages }))
          : unknown.client.getThreadLiveState(owner.threadId, readAbort.signal);
        // Physical occupancy remains reserved until the underlying read settles,
        // even if a test/client ignores cancellation after the visible deadline.
        void reading.finally(() => { unknown.checking = false; this.publishAdmission(); }).catch(() => {});
        const deadline = new Promise<never>((_resolve, reject) => {
          timer = setTimeout(() => { readAbort.abort(); reject(new Error("Receipt check timed out")); }, 4000);
        });
        const thread = await Promise.race([reading, deadline]);
        if (this.disposed || owner.unknown !== unknown || !this.pendingAdmissions.has(owner)) return;
        if (!unknown.messageId) {
          this.ui.showInfo("Live status checked. It cannot confirm receipt of this decision; the decision has not been resent.");
          continue;
        }
        if (!("messages" in thread) || !thread.messages.some(message => message.id === unknown.messageId)) continue;
        unknown.accepted?.();
        delete owner.unknown;
        this.admitTurn(owner);
        this.finishTurn(owner);
        if (!this.turnOperation && !this.pendingAdmissions.size) this.ui.setChatInputEnabled(true);
        this.lastLiveSignature = null;
        void this.pollThreadLiveState();
        if (this.activeThreadId === owner.threadId && this.settings.getBackendBaseUrl() === owner.url) {
          this.ui.showInfo("The backend confirmed receipt of the pending message.");
          this.ui.setChatInputEnabled(true);
          this.lastLiveSignature = null;
          this.reconcilePending = true;
          void this.pollThreadLiveState();
        }
      } catch { /* Still unknown. No POST, draft restore or speculative retry. */ }
      finally {
        clearTimeout(timer);
        owner.abort.signal.removeEventListener("abort", onDispose);
        this.publishAdmission();
      }
    }
  }
  private seenStepIds = new Set<string>();
  private latestLiveReview: { taskId: string; shadowWorkspacePath: string | null } | null = null;
  private livePollInFlight = false;
  private readonly contextState = new ContextUsageState();
  private contextSignature = "";
  private streamEpoch = 0;
  private contextPollEpoch = 0;

  private contextView(reset = false): number {
    const url = this.settings.getBackendBaseUrl();
    if (reset || this.contextState.thread !== this.activeThreadId || this.contextState.url !== url) {
      this.historyReader.cancel();
      this.contextState.navigate(this.activeThreadId, url);
      if (!this.disposed && this.historyEpoch >= 0) {
        this.historyEpoch = this.contextState.epoch;
        this.historyLoaded = false; this.historyLiveAt = 0; this.historyFailed = false;
        this.historyProjection = ""; this.clearActionableHistory(); this.publishHistory();
      }
      this.livePollInFlight = false;
      this.streamEpoch++;
      if (this.turnOperation) {
        this.turnOperation.retired = true;
        // Headers may still be pending after the server launched work. Keep that
        // owner registered across A→B→A; retire only its visible attachment.
        if (this.turnOperation.admitted) this.turnOperation.abort.abort();
        this.turnOperation = null;
      }
    }
    this.renderContext();
    return this.contextState.epoch;
  }

  private renderContext(force = false): void {
    const signature = JSON.stringify([this.contextState.usage, this.contextState.stale]);
    if (!force && signature === this.contextSignature) return;
    this.contextSignature = signature;
    this.ui.renderContextUsage(this.contextState.usage, this.contextState.stale);
  }

  async reconnectContextUsage(): Promise<void> {
    this.contextView(true);
    if (this.activeThreadId) this.attachHistory(this.activeThreadId);
    this.lastLiveSignature = null;
    await this.pollThreadLiveState();
  }

  async refreshContextUsage(): Promise<void> {
    // Backend identity/generation decides invalidation. Preserve no-op/effort-only
    // observations, but supersede pre-mutation polls before obtaining a fresh snapshot.
    this.contextView();
    ++this.contextPollEpoch;
    this.livePollInFlight = false;
    await this.pollThreadLiveState();
  }
  // Tracks which task id the seenStepIds set belongs to, independently of deviationsTaskId.
  private stepTrackingTaskId: string | null = null;

  constructor(
    private readonly createClient: BackendClientFactory,
    private readonly sessionStore: SessionStore,
    private readonly settings: SettingsProvider,
    private readonly ui: ControllerUI,
    private readonly diffService: DiffService,
    private readonly now: () => string = () => new Date().toISOString()
  ) {}

  async initialize(): Promise<void> {
    const restored = await this.sessionStore.load();
    if (!restored) {
      return;
    }

    this.session = restored;
    if (!shouldStopPolling(restored.status)) {
      this.startPolling();
    }
    await this.refreshTask();
  }

  async startTask(): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    const workspacePath = this.ui.getWorkspacePath();
    if (!workspacePath) {
      this.ui.showError("Open a workspace folder before starting a Crucible task.");
      return;
    }

    const goal = (await this.ui.promptForGoal())?.trim();
    if (!goal) {
      return;
    }

    const backendBaseUrl = this.settings.getBackendBaseUrl();
    const mode = this.settings.getDefaultMode();
    const client = this.createClient(backendBaseUrl);

    let submission: { taskId: string };
    try {
      await verifyWork(); if (!isCurrentWork()) return;
      submission = await client.submitTask({
        goal,
        workspacePath,
        mode: mode satisfies TaskSubmission["mode"],
      });
    } catch (error) {
      this.ui.showError(`Failed to submit task: ${formatError(error)}`);
      return;
    }

    this.session = {
      taskId: submission.taskId,
      status: "QUEUED",
      workspacePath,
      backendBaseUrl,
      updatedAt: this.now(),
    };
    this.latestTask = {
      taskId: submission.taskId,
      goal,
      status: "QUEUED",
      modifiedFiles: [],
      diagnostics: [],
    };
    this.latestResult = null;
    this.stopStream();

    await this.sessionStore.save(this.session);
    this.startPolling();
    this.startStream(submission.taskId);
    this.ui.showInfo(`Started Crucible task ${submission.taskId}`);
  
});
}

  openReviewPanel(): void {
    // The unified chat panel replaced the review panel.
    this.ui.openChatPanel();
  }

  // ── Memory inspector (Phase 3-B) ────────────────────────────────────────────
  // vscode-free seam: the controller exposes the read-only data source + the active
  // thread + workspace; extension.ts owns MemoryPanel construction (it needs vscode +
  // extensionUri). The client is session-independent (built from the backend URL, like
  // attachToTask) since the inspector opens from chat with no task session.
  memoryDataSource(): MemoryDataSource {
    return {
      getInspect: (tid) =>
        tid ? this.memoryClient().getMemoryInspect(tid) : Promise.resolve(null),
      getSnapshot: (tid) => {
        const client = this.memoryClient();
        if (!client.getMemorySnapshot) throw new Error("Active memory inspection is unavailable from this backend.");
        return client.getMemorySnapshot(tid);
      },
      browse: (filter) => this.memoryClient().listMemories(filter),
      getChain: (memoryId) => this.memoryClient().getSupersedeChain(memoryId),
      getCuration: (tid) => {
        const client = this.memoryClient();
        if (!client.getMemoryCuration) throw new Error("Curation status is unavailable from this backend.");
        return client.getMemoryCuration(tid);
      },
    };
  }

  async buildDependencyIndex(action: () => Promise<void>): Promise<void> {
    if (this.historyEpoch < 0 || !this.activeThreadId) throw new Error("Open a connected conversation before building the index.");
    let dispatched = false, failed = false; let failure: unknown;
    await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (_verify, current) => {
      if (!current()) return; dispatched = true;
      try { await action(); } catch (error) { failed = true; failure = error; throw error; }
    });
    if (!dispatched) throw new Error("Wait for current project work and reconnect this conversation before building the index.");
    if (failed) throw failure;
  }

  memoryThreadId(): string {
    return this.activeThreadId ?? "";
  }

  memoryWorkspacePath(): string {
    return this.session?.workspacePath ?? this.ui.getWorkspacePath() ?? "";
  }

  private promptsDir(): string {
    return path.join(this.memoryWorkspacePath(), ".crucible", "prompts");
  }

  /** Names of available `.crucible/prompts/*.md` for composer `/` autocomplete. */
  async listPrompts(): Promise<string[]> {
    const ws = this.memoryWorkspacePath();
    if (!ws) return [];
    return listPromptNames(this.promptsDir());
  }

  /** Expand `/name args` to its substituted body; `found=false` when no such prompt. */
  async expandPrompt(name: string, args: string): Promise<{ found: boolean; text: string }> {
    const ws = this.memoryWorkspacePath();
    if (!ws) return { found: false, text: "" };
    const body = await loadPromptBody(this.promptsDir(), name);
    if (body === null) return { found: false, text: "" };
    return { found: true, text: substitutePrompt(body, args) };
  }

  /** Skill catalog (.crucible/skills/<name>/SKILL.md) for the composer's /skill forced-load. */
  async listSkills(): Promise<{ name: string; description: string }[]> {
    const ws = this.memoryWorkspacePath();
    if (!ws) return [];
    try {
      return await this.memoryClient().listSkills(ws);
    } catch {
      return [];
    }
  }

  private memoryClient(): BackendTaskClient {
    return this.createClient(this.settings.getBackendBaseUrl());
  }

  /** Session-independent client for config reads/hot-swap (composer model menu). */
  configClient(): BackendTaskClient {
    return this.createClient(this.settings.getBackendBaseUrl());
  }

  async attachToTask(): Promise<void> {
    const taskId = (await this.ui.promptForTaskId())?.trim();
    if (!taskId) {
      return;
    }

    const backendBaseUrl = this.settings.getBackendBaseUrl();
    const client = this.createClient(backendBaseUrl);

    let task: TaskView;
    try {
      task = await client.getTask(taskId);
    } catch (error) {
      this.ui.showError(`Task not found: ${formatError(error)}`);
      return;
    }

    const workspacePath = this.session?.workspacePath ?? this.ui.getWorkspacePath() ?? "";

    this.stopPolling();
    this.session = {
      taskId: task.taskId,
      status: task.status,
      workspacePath,
      backendBaseUrl,
      updatedAt: this.now(),
    };
    this.latestTask = task;
    this.latestResult = null;
    this.stopStream();

    await this.sessionStore.save(this.session);

    if (!shouldStopPolling(task.status)) {
      this.startPolling();
      this.syncStream(task.status, task.taskId);
    } else {
      await this.refreshTask();
    }

    this.ui.showInfo(`Attached to task ${taskId} (${task.status})`);
  }

  async refreshTask(): Promise<void> {
    await this.pullLatestTask();
  }

  async acceptPatch(): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    if (!this.session) {
      this.ui.showWarning("No active task to accept.");
      return;
    }

    const client = this.clientForSession();
    try {
      this.latestResult = await client.acceptPatch(this.session.taskId);
    } catch (error) {
      if (isConflictError(error)) {
        this.ui.showWarning("Task is no longer reviewable. Refreshing state.");
        await this.refreshTask();
        return;
      }
      this.ui.showError(`Failed to accept patch: ${formatError(error)}`);
      return;
    }

    await this.pullLatestTask();
    this.ui.showInfo("Patch accepted.");
  
});
}

  async rejectPatch(): Promise<void> {
    await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    if (!this.session) {
      this.ui.showWarning("No active task to reject.");
      return;
    }

    const reason = (await this.ui.promptForRejectReason())?.trim();
    if (!reason) {
      return;
    }

    const client = this.clientForSession();
    try {
      await verifyWork(); if (!isCurrentWork()) return;
      this.latestResult = await client.rejectPatch(this.session.taskId, reason);
    } catch (error) {
      if (isConflictError(error)) {
        this.ui.showWarning("Task is no longer reviewable. Refreshing state.");
        await this.refreshTask();
        return;
      }
      this.ui.showError(`Failed to reject patch: ${formatError(error)}`);
      return;
    }

    await this.pullLatestTask();
    this.ui.showInfo("Patch rejected.");
  
    });
  }

  async providePlanFeedback(feedback: string | null): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    if (!this.session) {
      this.ui.showWarning("No active task for plan feedback.");
      return;
    }

    const client = this.clientForSession();
    const trimmedFeedback = (feedback ?? "").trim();
    const normalizedFeedback = trimmedFeedback.length > 0 ? trimmedFeedback : null;
    try {
      const task = await client.providePlanFeedback(this.session.taskId, normalizedFeedback);
      this.latestTask = task;
      this.session = {
        ...this.session,
        status: task.status,
        updatedAt: this.now(),
      };
      await this.sessionStore.save(this.session);
      
      if (!shouldStopPolling(task.status)) {
        this.startPolling();
      }
      // The route returns the pre-transition status (AWAITING_PLAN_APPROVAL) because
      // continue_task runs in the background. Always start the stream here since
      // execution begins immediately after approval regardless of the returned status.
      this.stopStream();
      this.startStream(task.taskId);

      if (normalizedFeedback) {
        this.ui.showChatThinking("Regenerating plan with your feedback…");
        this.ui.setChatInputEnabled(false);
        this.ui.showInfo(`Submitted plan feedback. Regenerating...`);
      } else {
        this.ui.showInfo(`Plan approved. Proceeding to execution...`);
      }
    } catch (error) {
      this.ui.showError(`Failed to provide plan feedback: ${formatError(error)}`);
    }
  
});
}

  async resumeTask(): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    if (!this.session) {
      this.ui.showWarning("No active task to resume.");
      return;
    }
    const status = this.latestTask?.status;
    if (status !== "FAILED" && status !== "ABORTED") {
      this.ui.showWarning("Resume is only available for failed or aborted tasks.");
      return;
    }

    const stage = await this.ui.promptForResumeStage();
    if (!stage) return;

    let maxIterations: number | undefined;
    if (stage === "execute") {
      maxIterations = await this.ui.promptForMaxIterationsOverride();
    }

    const client = this.clientForSession();
    let response: ResumeTaskResponse;
    try {
      await verifyWork(); if (!isCurrentWork()) return;
      response = await client.resumeTask(this.session.taskId, {
        stage,
        budgetOverride: maxIterations !== undefined ? { maxIterations } : undefined,
      });
    } catch (error) {
      this.ui.showError(`Failed to resume task: ${formatError(error)}`);
      return;
    }

    // Switch session to the new child task
    const childInitialStatus: TaskStatus = stage === "feedback" ? "AWAITING_PLAN_APPROVAL" : "QUEUED";
    this.session = {
      ...this.session,
      taskId: response.taskId,
      status: childInitialStatus,
      updatedAt: this.now(),
    };
    this.latestTask = null;
    this.latestResult = null;
    await this.sessionStore.save(this.session);
    this.startPolling();
    this.ui.showInfo(`Resumed as new task ${response.taskId}`);
  
});
}

  async openInlineDiff(relativePath: string, shadowPath: string): Promise<void> {
    const workspacePath = this.ui.getWorkspacePath();
    // ReviewCard may post shadowPath "" — fall back to the latest live review's shadow path.
    if (!shadowPath && this.latestLiveReview?.shadowWorkspacePath) {
      const path = await import("node:path");
      shadowPath = path.join(this.latestLiveReview.shadowWorkspacePath, relativePath);
    }
    if (!workspacePath || !shadowPath) {
      this.ui.showWarning("Diff is unavailable — shadow path missing.");
      return;
    }
    const path = await import("node:path");
    const fs = await import("node:fs");
    const entry = {
      relativePath,
      realPath: path.join(workspacePath, relativePath),
      shadowPath,
      existsReal: fs.existsSync(path.join(workspacePath, relativePath)),
      existsShadow: fs.existsSync(shadowPath),
    };
    try {
      await this.diffService.openDiff(entry);
    } catch (error) {
      this.ui.showError(`Failed to open diff: ${formatError(error)}`);
    }
  }

  async openDiffForFile(relativePath: string): Promise<void> {
    if (!this.session || !this.latestResult) {
      this.ui.showWarning("No review result is available for diff inspection.");
      return;
    }

    if (!this.latestResult.shadowWorkspacePath) {
      this.ui.showWarning("Shadow workspace is unavailable for this task result.");
      return;
    }

    const entries = buildReviewFileEntries(
      this.session.workspacePath,
      this.latestResult.shadowWorkspacePath,
      this.latestResult.modifiedFiles
    );
    const entry = entries.find((candidate) => candidate.relativePath === relativePath);
    if (!entry) {
      this.ui.showWarning(`File not found in review list: ${relativePath}`);
      return;
    }

    try {
      await this.diffService.openDiff(entry);
    } catch (error) {
      this.ui.showError(`Failed to open diff: ${formatError(error)}`);
    }
  }

  async openChat(): Promise<void> {
    let epoch = this.contextView(true);
    const current = () => this.contextState.current(epoch, this.settings.getBackendBaseUrl());
    const workspacePath = this.ui.getWorkspacePath() ?? "";
    const client = this.createClient(this.settings.getBackendBaseUrl());
    // First-run gate: an unreachable backend or an unconfigured provider means the
    // user hasn't finished setup. Route them into the wizard rather than failing
    // later with a confusing "Failed to create chat thread" error.
    try {
      const config = await client.getConfig();
      if (!current()) return;
      if (!config.provider) {
        this.ui.promptSetup("Choose a model provider to start chatting.");
        return;
      }
    } catch {
      if (!current()) return;
      this.ui.promptSetup(
        "Crucible isn't set up yet — install the runtime and pick a model provider.",
      );
      return;
    }
    let threads: ChatThreadSummary[];
    try {
      threads = await client.listChatThreads(workspacePath);
    } catch {
      threads = [];
    }
    if (!current()) return;
    const first = threads[0];
    if (first && !this.activeThreadId) {
      this.activeThreadId = first.threadId;
    } else if (!this.activeThreadId) {
      try {
        const thread = await client.createChatThread(workspacePath);
        if (!current()) return;
        this.activeThreadId = thread.threadId;
        threads = [thread];
      } catch (error) {
        this.ui.showError(`Failed to create chat thread: ${formatError(error)}`);
        return;
      }
    }
    epoch = this.contextView();
    this.ui.openChatPanel();
    this.ui.renderChatThreadList(threads, this.activeThreadId ?? "");
    if (this.activeThreadId) {
      this.attachHistory(this.activeThreadId);
      await this.reconcileTranscript(this.activeThreadId, 0, true);
      if (!current()) return;
      void this.refreshAgentRoster(this.activeThreadId);
      void this.refreshTeams(this.activeThreadId);
    }
    if (!current()) return;
    // Re-derive any pending gate/plan from persisted state so cards survive the
    // reload/reopen that just discarded the SSE-rendered ones.
    this.lastLiveSignature = null;
    this.forgetUsage();
    this.startLiveStatePolling();
  }

  async newChatThread(): Promise<void> {
    let epoch = this.contextView(true);
    const current = () => this.contextState.current(epoch, this.settings.getBackendBaseUrl());
    const workspacePath = this.ui.getWorkspacePath() ?? "";
    const client = this.createClient(this.settings.getBackendBaseUrl());
    let thread;
    try {
      thread = await client.createChatThread(workspacePath);
    } catch (error) {
      if (!current()) return;
      this.ui.showError(`Failed to create chat thread: ${formatError(error)}`);
      return;
    }
    if (!current()) return;
    this.activeThreadId = thread.threadId;
    epoch = this.contextView();
    this.ui.openChatPanel();
    this.attachHistory(thread.threadId);
    this.historyLoaded = true; this.historySaved = true; this.lastReconciledCount = 0;
    this.historyCache.put(this.historyKey(thread.threadId), []);
    this.ui.clearLiveGates();
    this.ui.clearLivePlan();
    this.lastLiveSignature = null;
    this.agentViews.closeAll();
    this.teamViews.closeAll();
    this.lastLiveTeamIds = new Set();
    this.lastTurnActive = false;
    this.forgetUsage();
    let threads: ChatThreadSummary[];
    try {
      threads = await client.listChatThreads(workspacePath);
    } catch {
      threads = [thread];
    }
    if (!current()) return;
    this.ui.renderChatThreadList(threads, thread.threadId);
    this.startLiveStatePolling();
  }

  async switchChatThread(threadId: string): Promise<void> {
    this.activeThreadId = threadId;
    const epoch = this.contextView(true);
    this.attachHistory(threadId);
    this.renderContext(true);
    this.agentViews.closeAll(); this.teamViews.closeAll();
    this.lastLiveTeamIds = new Set(); this.lastTurnActive = false; this.forgetUsage();
    await this.reconcileTranscript(threadId, 0, true);
    if (!this.contextState.current(epoch, this.settings.getBackendBaseUrl())) return;
    void this.refreshAgentRoster(threadId); void this.refreshTeams(threadId);
    this.startLiveStatePolling();
  }

  /** The webview's full set of open agent views (inline boxes + the window). */
  setOpenAgents(agentIds: string[]): void {
    if (!this.activeThreadId) return;
    this.agentViews.setOpen(this.activeThreadId, agentIds);
  }
  /** The webview's full set of open team windows. */
  setOpenTeams(teamIds: string[]): void {
    if (!this.activeThreadId) return;
    this.teamViews.setOpen(this.activeThreadId, teamIds);
  }

  async disbandTeam(teamId: string): Promise<void> {
    const threadId = this.activeThreadId;
    if (!threadId) return;
    try {
      await this.clientForChat().disbandTeam(threadId, teamId);
    } catch (error) {
      this.ui.showError(`Failed to disband team: ${formatError(error)}`);
      return;
    }
    this.lastLiveSignature = null;
    void this.pollThreadLiveState();
    void this.refreshTeams(threadId);
  }

  async decideTeamPlan(
    threadId: string, gateId: string, decision: "approve" | "feedback" | "reject",
    feedback?: string,
  ): Promise<void> {

await this.runProjectWork(threadId, "team_plan", gateId, async (verifyWork, isCurrentWork) => {


    let dispatched = false;
    try {
      const expectedProcessId = await this.approvalReady();
      await verifyWork(); if (!isCurrentWork()) return;
      dispatched = true;
      if (expectedProcessId) await this.clientForChat().decideTeamPlan(threadId, gateId, decision, feedback, expectedProcessId);
      else await this.clientForChat().decideTeamPlan(threadId, gateId, decision, feedback);
    } catch (err) {
      if (dispatched && this.holdUnknownContinuation(err, "team_plan", gateId)) return;
      this.ui.retryDecision?.(threadId, "team_plan", gateId);
      if (await this.recoverApproval(err)) return;
      if (this.isBenignGateMiss(err)) return;   // the card was already answered or stale
      this.ui.showError(`Failed to send the plan decision: ${formatError(err)}`);
      return;
    }
    this.lastLiveSignature = null;
    void this.pollThreadLiveState();
  
});

}

  /** A fresh webview or another thread has no usage yet: the next poll fetches it and
   * re-sends it even when the numbers did not change. */
  private forgetUsage(): void {
    this.usageThreadId = null;
    this.lastUsageSignature = null;
  }

  private maybeRefreshUsage(threadId: string, live: ThreadLiveState): void {
    const busy = (live.turnActive ?? false) || (live.agentsRunning ?? 0) > 0;
    const now = Date.now();
    const due = threadId !== this.usageThreadId
      || (busy && now - this.usageFetchedAt >= USAGE_REFRESH_MS)
      || (this.usageWasBusy && !busy);
    this.usageWasBusy = busy;
    if (!due) return;
    if (threadId !== this.usageThreadId) {
      this.lastUsageSignature = null;
      this.ui.renderThreadUsage(null);
    }
    this.usageThreadId = threadId;
    this.usageFetchedAt = now;
    void this.refreshUsage(threadId);
  }

  private async refreshUsage(threadId: string): Promise<void> {
    let usage: ThreadUsage;
    try {
      usage = await this.clientForChat().getThreadUsage(threadId);
    } catch {
      return; // transient, or a backend without the route; the next due tick retries
    }
    if (threadId !== this.activeThreadId) return;
    const signature = JSON.stringify(usage);
    if (signature === this.lastUsageSignature) return;
    this.lastUsageSignature = signature;
    this.ui.renderThreadUsage(usage);
  }

  /** Team summaries from the routes (spec §9): thread open, turn end, a team ending. */
  private async refreshTeams(threadId: string): Promise<void> {
    let teams: TeamSummary[];
    try {
      teams = await this.clientForChat().listTeams(threadId);
    } catch {
      return; // transient, or a backend without teams
    }
    if (threadId !== this.activeThreadId) return;
    if (teams.length > 0) this.ui.renderTeams(teams);
    for (const team of teams) this.teamViews.noteState(threadId, team.teamId, team);
  }


  async stopAllAgents(): Promise<void> {
    const threadId = this.activeThreadId;
    if (!threadId) return;
    try {
      await this.clientForChat().stopAllAgents(threadId);
    } catch (error) {
      this.ui.showError(`Failed to stop agents: ${formatError(error)}`);
      return;
    }
    this.lastLiveSignature = null;
    void this.pollThreadLiveState();
  }

  /** Reconcile by replacement (spec §6): messages written while nothing streamed this
   * thread — a notice marker, a breadcrumb — reach the webview without a reload. Swaps
   * the messages only; agents, views, live cards and subscriptions are untouched. */
  private readSavedHistory(threadId: string, client = this.clientForChat(), key = this.historyKey(threadId)): Promise<ChatMessage[]> {
    return this.historyReader.read(key, async signal => {
      const thread = await client.getChatThread(threadId, signal);
      if (thread.threadId !== threadId) throw new Error("History identity mismatch");
      return thread.messages;
    });
  }

  private async reconcileTranscript(threadId: string, expected: number, force = false): Promise<void> {
    const epoch = this.contextView();
    if (this.activeThreadId !== threadId || this.disposed || this.turnAbort || this.pendingAdmissions.size) {
      this.reconcilePending = true; return;
    }
    if (Date.now() < this.historyNextAt && (this.historyFailures > 0 || !force && expected === this.lastReconciledCount)) return;
    const key = this.historyKey(threadId), mutation = this.historyMutation;
    const process = this.historyProcess, readOwner = ++this.historyReadOwner;
    const current = () => !this.disposed && this.contextState.current(epoch, this.settings.getBackendBaseUrl())
      && this.activeThreadId === threadId && key === this.historyKey(threadId)
      && mutation === this.historyMutation && !this.turnAbort && !this.pendingAdmissions.size
      && process === this.historyProcess && readOwner === this.historyReadOwner;
    this.historyNextAt = Date.now() + 2000;
    try {
      const messages = await this.readSavedHistory(threadId, this.clientForChat(), key);
      if (!current()) return;
      const echoes = this.historyEchoes.get(key);
      if (echoes) {
        for (const message of messages) if (message.id) echoes.delete(message.id);
        if (echoes.size) { this.reconcilePending = true; return; }
        this.historyEchoes.delete(key);
      }
      this.historyLoaded = true; this.historySaved = true; this.historyFailed = false; this.historyFailures = 0;
      this.historyNotice = this.historyCache.put(key, messages) ? undefined : "This conversation is too large to keep an extra saved copy.";
      this.lastReconciledCount = messages.length; this.reconcilePending = false;
      this.ui.replaceChatMessages(messages); this.publishHistory();
    } catch {
      if (!current()) return;
      this.historyFailed = true;
      this.historyNextAt = Date.now() + Math.min(15000, 2000 * 2 ** Math.min(this.historyFailures++, 3));
      this.clearActionableHistory(); this.publishHistory();
    }
  }

  onAttention(listener: (threadId: string, pendingGates: number) => void): void {
    this.attentionListeners.push(listener);
  }

  /** Threads other than the open one that gained a pending card (spec §6). */
  async pollAttention(): Promise<void> {
    const workspace = this.ui.getWorkspacePath();
    if (!workspace) return;
    let rows: ThreadAttention[];
    try {
      rows = await this.clientForChat().getAttention(workspace);
    } catch {
      return;
    }
    const seen = new Set<string>();
    for (const row of rows) {
      seen.add(row.threadId);
      if (row.threadId === this.activeThreadId || row.pendingGates === 0) {
        this.attentionNotified.delete(row.threadId);
        continue;
      }
      if (this.attentionNotified.get(row.threadId) === row.pendingGates) continue;
      this.attentionNotified.set(row.threadId, row.pendingGates);
      for (const listener of this.attentionListeners) listener(row.threadId, row.pendingGates);
    }
    for (const id of [...this.attentionNotified.keys()]) {
      if (!seen.has(id)) this.attentionNotified.delete(id);
    }
  }

  async stopAgent(agentId: string): Promise<void> {
    const threadId = this.activeThreadId;
    if (!threadId) return;
    try {
      await this.clientForChat().stopAgent(threadId, agentId);
    } catch (error) {
      this.ui.showError(`Failed to stop agent: ${formatError(error)}`);
      return;
    }
    this.lastLiveSignature = null;
    void this.pollThreadLiveState();
  }

  /** The thread's whole roster from the routes — the source once a turn is over (§11.1). */
  private async refreshAgentRoster(threadId: string): Promise<void> {
    let agents: AgentSummary[];
    try {
      agents = await this.clientForChat().listAgents(threadId);
    } catch {
      return; // transient, or a backend without agents: roster rows show placeholders
    }
    if (threadId !== this.activeThreadId) return;
    if (agents.length > 0) this.ui.renderAgents(agents);
    for (const agent of agents) this.agentViews.noteStatus(agent.agentId, agent.status);
  }

  async previewRewind(messageId: string): Promise<void> {
    if (!this.historyActionReady()) { this.ui.showWarning("Reconnect saved history before rewinding."); return; }
    if (!this.activeThreadId) return;
    const client = this.createClient(this.settings.getBackendBaseUrl());
    const epoch = this.contextView();
    const mutation = this.historyMutation, process = this.historyProcess;
    const threadId = this.activeThreadId;
    try {
      const preview = await client.previewRewind(threadId, messageId);
      if (!this.contextState.current(epoch, this.settings.getBackendBaseUrl()) || !this.historyActionReady() || mutation !== this.historyMutation || process !== this.historyProcess) return;
      this.ui.showRewindPreview(preview);
    } catch (error) {
      this.ui.showError(`Could not preview rewind: ${formatError(error)}`);
    }
  }

  async rewindTo(messageId: string): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    if (!this.historyActionReady()) { this.ui.showWarning("Reconnect saved history before rewinding."); return; }
    const threadId = this.activeThreadId;
    if (!threadId) return;
    const client = this.createClient(this.settings.getBackendBaseUrl());
    const epoch = this.contextView();
    const mutation = this.historyMutation, process = this.historyProcess;
    const imageAttachments: { name: string; mediaType: string; dataUrl: string; bytes: number }[] = [];
    let result;
    try {
      const original = (await this.readSavedHistory(threadId, client)).find(m => m.id === messageId);
      if (!this.contextState.current(epoch, this.settings.getBackendBaseUrl())) return;
      const previews = original?.metadata?.["image_previews"];
      if (Array.isArray(previews)) {
        for (const preview of previews) {
          if (typeof preview?.dataUrl !== "string" || !preview.dataUrl.startsWith("data:image/png;base64,")) continue;
          const encoded = preview.dataUrl.split(",")[1] ?? "";
          imageAttachments.push({ name: String(preview.name ?? "Image"), mediaType: "image/png", dataUrl: preview.dataUrl,
            bytes: Buffer.from(encoded, "base64").length });
        }
        if (previews.some(p => !p?.dataUrl)) this.ui.showError("Some rewound image previews are unavailable; reattach those images before sending.");
      }
      if (!this.historyActionReady() || mutation !== this.historyMutation || process !== this.historyProcess
          || !this.contextState.current(epoch, this.settings.getBackendBaseUrl())) {
        this.ui.showWarning("Reconnect saved history before rewinding."); return;
      }
      await verifyWork(); if (!isCurrentWork()) return;
      this.historyMutation++; this.historyReader.cancel();
      result = await client.rewindThread(threadId, messageId);
    } catch (error) {
      // A refusal (409 turn in flight / live task) has to be visible. Falling through
      // to a reload here would look exactly like a rewind that worked.
      this.ui.showError(`Rewind failed: ${formatError(error)}`);
      return;
    }
    // The server is authoritative about what survived, so reload rather than mutating
    // the transcript locally. Mirrors switchChatThread's clear-then-append.
    if (!this.contextState.current(epoch, this.settings.getBackendBaseUrl())) return;
    const reopening = this.switchChatThread(threadId);
    const reopenEpoch = this.contextState.epoch;
    await reopening;
    if (this.activeThreadId !== threadId || !this.contextState.current(reopenEpoch, this.settings.getBackendBaseUrl())) return;
    this.ui.prefillComposer(result.prefillText, { threadId, imageAttachments });
    if (result.failed.length > 0) {
      this.ui.showError(
        `Rewind restored ${result.restoredFiles.length} file(s) but ${result.failed.length} `
        + `could not be restored: ${result.failed.map((f) => f.path).join(", ")}`,
      );
    }
  
});
}

  async sendChatMessage(
    text: string, stepReview?: boolean, forcedSkills?: string[], mentionedPaths?: string[],
    planMode?: boolean,
    imageAttachments?: { name: string; mediaType: string; dataUrl: string; bytes: number }[],
    draftId?: string,
    originalDraftText?: string,
  ): Promise<void> {
const originThread = this.activeThreadId, originWorkspace = this.ui.getWorkspacePath(), originUrl = this.settings.getBackendBaseUrl();
let entered = false;
await this.runProjectWork(this.activeThreadId ?? "", "message", undefined, async (verifyWork, isCurrentWork) => {
entered = true;

    if (this.disposed) return;
    const workspacePath = this.ui.getWorkspacePath() ?? "";
    const client = this.createClient(this.settings.getBackendBaseUrl());
    let sendEpoch = this.contextView();
    const sendMutation = this.historyMutation, sendProcess = this.historyProcess;
    const currentSend = () => !this.disposed && this.contextState.current(sendEpoch, this.settings.getBackendBaseUrl()) && this.ui.getWorkspacePath() === workspacePath;
    // InputArea already cleared the draft: every refusal gives the text back (spec §5.3).
    const restoreDraft = () => {
      const recovery = draftId ? { draftId, imageAttachments: imageAttachments ?? [], ...(mentionedPaths ? { mentionedPaths } : {}) } : undefined;
      if (currentSend()) this.ui.restoreDraft(originalDraftText ?? text, recovery);
      else this.ui.staleDraft?.(originalDraftText ?? text, recovery);
    };
    const staleSend = () => {
      if (currentSend()) return false;
      this.ui.staleDraft?.(originalDraftText ?? text, draftId ? { draftId, imageAttachments: imageAttachments ?? [], ...(mentionedPaths ? { mentionedPaths } : {}) } : undefined);
      return true;
    };
    if (this.historyEpoch === this.contextState.epoch && (!this.historyLoaded || this.historyFailed
        || !this.historyLiveAt || Date.now() - this.historyLiveAt >= 5000)) {
      restoreDraft(); this.ui.showWarning("Wait for saved history and live status to reconnect before sending."); return;
    }
    if (this.activeThreadId && [...this.historyEchoes.values()].reduce((count, ids) => count + ids.size, 0) >= 128) {
      restoreDraft(); this.ui.showWarning("Wait for queued messages to appear in saved history before sending more."); return;
    }
    // One unresolved POST per controller/project, across visible attachments.
    // Keep both hosts' full original payload in memory if receipt becomes unknown.
    const submission = [text, stepReview, forcedSkills, mentionedPaths, planMode, imageAttachments, draftId, originalDraftText] as const;
    if (this.pendingAdmissions.size || Buffer.byteLength(JSON.stringify(submission), "utf8") > 64 * 1024 * 1024) {
      restoreDraft();
      this.ui.showWarning(this.pendingAdmissions.size
        ? "A request's receipt is still pending. Refresh to check; do not send it again."
        : "This draft exceeds the 64 MiB in-memory submission limit.");
      return;
    }
    const pinnedSubmission = structuredClone(submission);
    let expectedProcessId: string | undefined;
    try { if (this.approval) expectedProcessId = await this.approvalReady(); }
    catch (error) {
      if (staleSend()) return;
      if (this.activeThreadId) this.ui.retryDecision?.(this.activeThreadId, "mode");
      restoreDraft();
      this.ui.setChatInputEnabled(true);
      this.ui.showError(`Approval mode not confirmed — retry: ${formatError(error)}`);
      return;
    }
    if (this.approval && !(await this.approval.confirmFirstSend())) {
      if (staleSend()) return;
      if (this.activeThreadId) this.ui.retryDecision?.(this.activeThreadId, "mode");
      restoreDraft();
      this.ui.setChatInputEnabled(true);
      return;
    }

    if (staleSend()) return;
    if (!this.historyActionReady() || sendMutation !== this.historyMutation || sendProcess !== this.historyProcess) { restoreDraft(); this.ui.showWarning("Reconnect saved history before sending."); return; }
    if (this.pendingAdmissions.size) {
      restoreDraft();
      this.ui.showWarning("Another request is awaiting receipt confirmation. Refresh before sending more work.");
      return;
    }
    if (!this.activeThreadId) {
      try {
        const thread = await client.createChatThread(workspacePath);
        if (draftId) this.ui.draftCreated?.(draftId, thread.threadId);
        if (staleSend()) return;
        this.activeThreadId = thread.threadId;
        sendEpoch = this.contextView();
        this.ui.openChatPanel();
      } catch (error) {
        if (staleSend()) return;
        this.ui.showError(`Failed to create chat thread: ${formatError(error)}`);
        restoreDraft();
        return;
      }
    }

    if (!this.historyActionReady() || sendMutation !== this.historyMutation || sendProcess !== this.historyProcess) { restoreDraft(); this.ui.showWarning("Reconnect saved history before sending."); return; }
    try { await verifyWork(); if (!isCurrentWork()) return; }
    catch (error) { restoreDraft(); this.ui.showWarning(formatError(error)); return; }
    const threadId = this.activeThreadId;
    const mentionedFiles =
      mentionedPaths && mentionedPaths.length && workspacePath
        ? readMentionedFiles(workspacePath, mentionedPaths)
        : undefined;

    // The echoed bubble and the persisted message must share an id, or the rewind
    // affordance is missing on the message you just sent until the thread reloads
    // (the same optimistic-echo gap that once hid @-mention links). We choose the id
    // here and hand it to the backend, which honours it.
    const messageId = randomUUID().replace(/-/g, "");
    const echoKey = this.historyKey(threadId);
    const echoes = this.historyEchoes.get(echoKey) ?? new Set<string>();
    echoes.add(messageId); this.historyEchoes.set(echoKey, echoes);
    this.ui.appendChatMessage({
      role: "user",
      content: text,
      type: "text",
      id: messageId,
      timestamp: this.now(),
      metadata: {
        ...(mentionedPaths?.length ? { mentioned_files: mentionedPaths } : {}),
        ...(imageAttachments?.length ? { image_previews: imageAttachments.map(({ name, dataUrl }) => ({ name, dataUrl })) } : {}),
      },
    });

    this.ui.setChatInputEnabled(false);
    const owner = this.beginTurnOperation(threadId, true);
    let acknowledged = false;
    const accepted = () => {
      if (this.disposed) { owner.abort.abort(); return; }
      this.admitTurn(owner, false);
      if (!acknowledged) { acknowledged = true; if (draftId) this.ui.draftAccepted?.(draftId, threadId); }
    };
    let result: SendChatResult;
    try {
      result = await client.sendChatMessage(threadId, text, owner.abort.signal, {
        onAdmitted: accepted,
        ...(expectedProcessId ? { expectedProcessId } : {}),
        ...(stepReview !== undefined ? { stepReview } : {}),
        ...(forcedSkills?.length ? { forcedSkills } : {}),
        ...(mentionedFiles?.length ? { mentionedFiles } : {}),
        ...(imageAttachments?.length ? { imageAttachments } : {}),
        ...(planMode !== undefined ? { planMode } : {}),
        messageId,
      });
    } catch (error) {
      if (this.disposed) { this.finishTurn(owner); return; }
      if (!owner.admitted && error instanceof ChatAdmissionUnknownError) {
        owner.unknown = { client, messageId, accepted, submission: pinnedSubmission, checking: false };
        this.publishAdmission();
        if (currentSend()) {
          this.ui.showWarning("Message receipt is unknown. The original draft is retained; use Check receipt without resending.");
          this.ui.setChatInputEnabled(false);
        }
        return;
      }
      this.finishTurn(owner);
      if (owner.admitted) {
        if (currentSend()) {
          this.ui.showWarning("Message accepted; its response was interrupted. Refresh to recover the conversation.");
          this.ui.setChatInputEnabled(true);
          this.lastLiveSignature = null;
          void this.pollThreadLiveState();
        }
        return;
      }
      if (staleSend()) return;
      // A definite rejection returns the original draft, not an uncertain send.
      this.historyEchoes.get(echoKey)?.delete(messageId);
      this.ui.removeChatMessage(messageId);
      if (this.activeThreadId) this.ui.retryDecision?.(this.activeThreadId, "mode");
      restoreDraft();
      this.ui.setChatInputEnabled(true);
      if (await this.recoverApproval(error) || !currentSend()) return;
      if (!this.isBenignConflict(error)) {
        this.ui.showError(`Failed to send: ${formatError(error)}`);
      }
      return;
    }
    accepted(); // Also supports older clients without the header callback.
    this.admitTurn(owner);
    if (!currentSend()) { this.finishTurn(owner); return; }
    if (result.kind === "queued") {
      // A notice turn took the message; its relay is already live (spec §5.3). The
      // bubble says it is queued until that turn takes it.
      this.ui.markQueued(messageId);
      this.finishTurn(owner);
      this.ui.setChatInputEnabled(true);
      return;
    }
    try { await this.streamTurn(result.events, owner); }
    catch (error) {
      // Revalidate after the outer await: navigation may occur in the microtask
      // between iterator failure and this caller receiving its rejection.
      if (!currentSend()) return;
      this.ui.showError(`Stream error: ${formatError(error)}`);
    }
  
});
if (!entered) {
  const recovery = draftId ? { draftId, imageAttachments: imageAttachments ?? [], ...(mentionedPaths ? { mentionedPaths } : {}) } : undefined;
  if (originThread === this.activeThreadId && originWorkspace === this.ui.getWorkspacePath() && originUrl === this.settings.getBackendBaseUrl()) this.ui.restoreDraft(originalDraftText ?? text, recovery);
  else this.ui.staleDraft?.(originalDraftText ?? text, recovery);
}
}

  /**
   * Consume a chat-turn SSE stream and render its events. Shared by sendChatMessage
   * and the controller mode-decision dispatch — both stream the same chat events.
   * The caller disables input + sets `turnAbort`; this owns the loop and teardown.
   */
  private async streamTurn(stream: AsyncIterable<StreamEvent>, owner: TurnOperation): Promise<void> {
    const epoch = owner.epoch;
    const url = owner.url;
    const streamEpoch = ++this.streamEpoch;
    const current = () => !this.disposed && !owner.retired && this.turnOperation === owner && this.contextState.current(epoch, this.settings.getBackendBaseUrl()) && this.streamEpoch === streamEpoch;
    let currentTaskId: string | undefined;
    let completed = false;
    try {
      this.openToolEvent = {}; // defensive: clear any stale ids from a previous turn
      // Clear the token counter HERE, at turn start — NOT in the finally below. The
      // closing tick carries the only exact count the provider ever reports, and
      // clearing on turn END unmounted it at the moment it became correct: the number
      // flashed and vanished, leaving "what did that cost?" unanswerable. Clearing on
      // the next turn's start still prevents a stale count carrying over, which is what
      // the turn-end clear was actually protecting against.
      this.ui.updateTokenProgress(null);
      for await (const event of stream) {
        if (!current()) break;
        if (event.type === "context_usage") {
          const result = this.contextState.accept(event.payload, epoch, url, "sse");
          this.renderContext();
          if (result === "hydrate") void this.pollThreadLiveState();
        } else if (event.type === "chat_agent_thinking") {
          const message = (event.payload["message"] as string) ?? "Thinking…";
          this.ui.appendChatThinkingEntry(message);
        } else if (event.type === "chat_agent_thinking_chunk") {
          const chunk = (event.payload["chunk"] as string) ?? "";
          if (chunk) this.ui.appendChatThinkingChunk(chunk);
        } else if (event.type === "tool_thinking_chunk") {
          const chunk = (event.payload["chunk"] as string) ?? "";
          if (chunk) this.ui.appendChatThinkingChunk(chunk);
        } else if (event.type === "token_progress") {
          const p = event.payload as {
            thinking?: number;
            output?: number;
            input?: number | null;
            exact?: boolean;
          };
          this.ui.updateTokenProgress({
            thinking: p.thinking ?? 0,
            output: p.output ?? 0,
            input: p.input ?? null,
            exact: p.exact ?? false,
          });
        } else if (event.type === "edit_failed") {
          const p = event.payload as { reason?: string; ops?: number };
          this.ui.updateEditFailure({ reason: p.reason ?? "", ops: p.ops ?? 0 });
        } else if (event.type === "retry_status") {
          const p = event.payload as {
            attempt?: number; max_attempts?: number; reason?: string; message?: string;
          };
          this.ui.updateRetryStatus({
            attempt: p.attempt ?? 0,
            max_attempts: p.max_attempts ?? 0,
            reason: p.reason ?? "",
            message: p.message ?? "",
          });
        } else if (event.type === "explore_tool_call") {
          this.forwardToolCall("explore", event.payload as Record<string, unknown>);
        } else if (event.type === "explore_tool_result") {
          this.forwardToolResult("explore", event.payload as Record<string, unknown>);
        } else if (event.type === "tool_call") {
          this.forwardToolCall("execution", event.payload as Record<string, unknown>);
        } else if (event.type === "tool_result") {
          this.forwardToolResult("execution", event.payload as Record<string, unknown>);
        } else if (event.type === "patch_applied") {
          this.ui.appendChatThinkingEntry("patch applied");
        } else if (event.type === "intent_classified") {
          // no-op: intent classification doesn't need a persistent entry
        } else if (event.type === "chat_response") {
          const chunk = (event.payload["chunk"] as string) ?? "";
          this.ui.appendChatChunk(chunk);
        } else if (event.type === "chat_breadcrumb") {
          // Controller decisions (mode choice, edit accept/reject) broadcast their
          // breadcrumbs on THIS turn stream. streamTurn previously had no branch for
          // them, so they only appeared on reload (persisted) — render live too.
          this.ui.appendChatMessage({
            role: "agent",
            content: (event.payload["text"] as string) ?? "",
            type: "text",
            taskId: (event.payload["task_id"] as string) ?? "",
            timestamp: this.now(),
            metadata: event.payload["edit_outcome"] === true
              ? { breadcrumb: true, edit_outcome: true }
              : { breadcrumb: true },
          });
        } else if (event.type === "chat_progress") {
          // Non-terminal mid-turn status note. Render live; the durable copy
          // (metadata.progress) arrives on reload from the backend persist.
          this.ui.appendChatMessage({
            role: "agent",
            content: (event.payload["note"] as string) ?? "",
            type: "text",
            taskId: "",
            timestamp: this.now(),
            metadata: { progress: true },
          });
        } else if (event.type === "memory_compacted") {
          // Observability: the memory harness compacted older history into the anchored
          // summary. Render a subtle system line so the user can see it fired.
          const evicted = (event.payload["evicted"] as number) ?? 0;
          const version = (event.payload["anchor_version"] as number) ?? 0;
          this.ui.appendChatMessage({
            role: "agent",
            content: `🗜️ Compacted ${evicted} earlier message${evicted === 1 ? "" : "s"} into memory (v${version})`,
            type: "text",
            timestamp: this.now(),
            metadata: { breadcrumb: true },
          });
        } else if (event.type === "diff_ready") {
          const taskId = (event.payload["task_id"] as string) ?? "";
          // `resolved` is set by the chat controller's auto-accept edit path (the
          // change is already promoted) → the card renders inert. The legacy inline
          // path omits it → interactive Accept/Reject, as before.
          const resolved = event.payload["resolved"] as
            | "applied"
            | "discarded"
            | undefined;
          this.ui.appendChatMessage({
            role: "agent",
            content: taskId,
            type: "diff_card",
            taskId,
            timestamp: this.now(),
            metadata: {
              diff_entries: event.payload["diff_entries"],
              thinking_log: event.payload["thinking_log"] ?? [],
              ...(resolved ? { resolved } : {}),
            },
          });
        } else if (event.type === "task_card") {
          currentTaskId = (event.payload["task_id"] as string) ?? "";
          this.ui.appendChatMessage({
            role: "agent",
            content: currentTaskId,
            type: "task_card",
            taskId: currentTaskId,
            timestamp: this.now(),
            metadata: {},
          });
        } else if (event.type === "planning_thinking_chunk") {
          const chunk = (event.payload["chunk"] as string) ?? "";
          if (chunk) this.ui.appendChatThinkingChunk(chunk);
        } else if (event.type === "planning_tool_call") {
          this.forwardToolCall("planning", event.payload as Record<string, unknown>);
          this.ui.updateWorkbar({ phaseLabel: `Planning: ${(event.payload["tool"] as string) ?? ""}…` });
        } else if (event.type === "planning_tool_result") {
          this.forwardToolResult("planning", event.payload as Record<string, unknown>);
        } else if (event.type === "planning_complete") {
          const confidence = (event.payload["confidence"] as string) ?? "";
          this.ui.appendChatThinkingEntry(`plan ready (${confidence} confidence)`);
          this.ui.updateWorkbar(null);
        } else if (event.type === "task_status_changed") {
          const taskId = (event.payload["task_id"] as string) ?? "";
          const status = (event.payload["status"] as string) ?? "";
          const planMarkdown = event.payload["plan_markdown"] as string | undefined;
          if (planMarkdown) {
            // Plan is a Class-A live card — let /live render it in the pinned slot.
            void this.pollThreadLiveState();
          } else {
            this.ui.appendChatMessage({
              role: "agent",
              content: `Task ${taskId}: ${status}`,
              type: "text",
              taskId,
              timestamp: this.now(),
              metadata: {},
            });
          }
        } else if (event.type === "plan_card") {
          // Read-only transcript record, delivered live (display-only — the backend
          // already persisted the single copy). chat.js dedups by task id, and the
          // interactive Implement/Feedback affordance is the pinned /live slot.
          this.ui.appendChatMessage({
            role: "agent",
            content: event.payload.plan_markdown,
            type: "plan_card",
            taskId: event.payload.task_id,
            timestamp: this.now(),
            metadata: { taskId: event.payload.task_id, plan_markdown: event.payload.plan_markdown },
          });
        } else if (event.type === "scope_extension_requested") {
          // Class-A gate — render from /live (instant poke now, durable on reload).
          this.forwardGateWait("scope");
        } else if (event.type === "validation_decision_requested") {
          this.forwardGateWait("validation");
        } else if (event.type === "command_approval_requested") {
          this.forwardGateWait("command");
        } else if (event.type === "mcp_approval_requested") {
          this.forwardGateWait("mcp_tool");
        } else if (event.type === "thread_title_updated") {
          const threadId = (event.payload["thread_id"] as string) ?? "";
          const title = (event.payload["title"] as string) ?? "";
          this.ui.updateThreadTitle(threadId, title);
        } else if (
          event.type === "env_profile_building" ||
          event.type === "env_profile_built" ||
          event.type === "env_install_running" ||
          event.type === "env_install_done"
        ) {
          this.forwardEnvEvent({ type: event.type, payload: event.payload as Record<string, unknown> });
        } else if (
          event.type === "agent_started" ||
          event.type === "agent_status" ||
          event.type === "agent_finished"
        ) {
          // Low-volume roster pokes (spec §11.2): /live is the source of truth.
          void this.pollThreadLiveState();
        } else if (
          event.type === "agent_dispatch" || event.type === "agent_message" ||
          event.type === "notice" || event.type === "team_created"
        ) {
          this.ui.appendChatMessage(parseWireChatMessage(event.payload.message));
        } else if (event.type === "chat_done") {
          completed = true;
          this.ui.updateWorkbar(null);
          this.ui.finalizeAgentMessage();
          break;
        }
      }
    } catch (error) {
      if (!this.disposed && error instanceof ChatAdmissionUnknownError && !owner.admitted) {
        // Retain even a retired pending decision before finally can release it.
        owner.unknown = { client: this.createClient(owner.url), checking: false };
        throw error;
      }
      // Retired stream failures must not reach generic router recovery for B/new A.
      if (!current()) return;
      if (error instanceof Error && error.name === "AbortError") {
        // do nothing
      } else {
        this.contextState.fail(epoch, url);
        this.renderContext();
        throw error;
      }
    } finally {
      if (current() && !owner.unknown) {
      if (!completed) { this.contextState.fail(epoch, url); this.renderContext(); }
      this.openToolEvent = {}; // clear cross-turn stale ids
      this.ui.updateWorkbar(null);
      this.ui.hideChatThinking();
      this.ui.updateRetryStatus(null);
      // tokenProgress is deliberately NOT cleared here — see the turn-start clear above.
      this.ui.updateEditFailure(null);
      this.ui.finalizeAgentMessage();
      this.ui.setChatInputEnabled(true);
      }
      this.finishTurn(owner);
    }
  }

  async handlePlanCardAction(
    taskId: string,
    action: "implement" | "feedback",
    feedback?: string
  ): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    if (action === "implement") {
      const client = this.createClient(this.settings.getBackendBaseUrl());
      try {
        await client.providePlanFeedback(taskId, null);
      } catch (error) {
        this.ui.showError(`Failed to approve plan: ${formatError(error)}`);
        return;
      }
      await this.streamTaskIntoChatThread(taskId);
    } else {
      if (!feedback?.trim()) return;
      const client = this.createClient(this.settings.getBackendBaseUrl());
      try {
        await client.providePlanFeedback(taskId, feedback.trim());
      } catch (error) {
        this.ui.showError(`Failed to submit plan feedback: ${formatError(error)}`);
        return;
      }
      this.ui.setChatInputEnabled(false);
      this.ui.appendChatThinkingEntry("Replanning with feedback…");
      const abort = new AbortController();
      try {
        await client.streamPatch(taskId, (event) => {
          if (event.type === "planning_thinking_chunk") {
            const chunk = (event.payload["chunk"] as string) ?? "";
            if (chunk) this.ui.appendChatThinkingChunk(chunk);
          } else if (event.type === "planning_tool_call") {
            this.forwardToolCall("planning", event.payload as Record<string, unknown>);
            this.ui.updateWorkbar({ phaseLabel: `Planning: ${(event.payload["tool"] as string) ?? ""}…` });
          } else if (event.type === "planning_tool_result") {
            this.forwardToolResult("planning", event.payload as Record<string, unknown>);
          } else if (event.type === "planning_complete") {
            const confidence = (event.payload["confidence"] as string) ?? "";
            this.ui.appendChatThinkingEntry(`plan ready (${confidence} confidence)`);
            this.ui.updateWorkbar(null);
            abort.abort();
          }
        }, abort.signal);
      } catch {
        // AbortError when we cancel after planning_complete — expected
      } finally {
        this.openToolEvent = {}; // clear cross-turn stale ids
        this.ui.updateWorkbar(null);
      }
      try {
        const task = await client.getTask(taskId);
        if (task.status === "AWAITING_PLAN_APPROVAL" && task.planMarkdown) {
          // Plan is a Class-A live card — render it from /live in the pinned slot.
          void this.pollThreadLiveState();
        } else if (task.status === "FAILED") {
          this.ui.showError("Re-planning failed — the model used its full tool budget without producing a plan. Try submitting feedback again with more specific instructions.");
        }
      } finally {
        this.ui.setChatInputEnabled(true);
      }
    }
  
});
}

  async streamTaskIntoChatThread(taskId: string, openingEntry = "Generating execution plan…"): Promise<void> {
    const client = this.createClient(this.settings.getBackendBaseUrl());
    this.ui.setChatInputEnabled(false);
    this.ui.appendChatThinkingEntry(openingEntry);
    try {
      for await (const event of client.streamPatchEvents(taskId)) {
        if (event.type === "planning_thinking_chunk") {
          const chunk = (event.payload["chunk"] as string) ?? "";
          if (chunk) this.ui.appendChatThinkingChunk(chunk);
        } else if (event.type === "tool_thinking_chunk") {
          const chunk = (event.payload["chunk"] as string) ?? "";
          if (chunk) this.ui.appendChatThinkingChunk(chunk);
        } else if (event.type === "task_status_changed") {
          const msg = (event.payload["message"] as string | undefined) ?? (event.payload["status"] as string) ?? "";
          if (msg) this.ui.appendChatThinkingEntry(msg);
        } else if (event.type === "tool_call") {
          this.forwardToolCall("execution", event.payload as Record<string, unknown>);
        } else if (event.type === "tool_result") {
          this.forwardToolResult("execution", event.payload as Record<string, unknown>);
        } else if (event.type === "planning_tool_call") {
          this.forwardToolCall("planning", event.payload as Record<string, unknown>);
          this.ui.updateWorkbar({ phaseLabel: `Planning: ${(event.payload["tool"] as string) ?? ""}…` });
        } else if (event.type === "planning_tool_result") {
          this.forwardToolResult("planning", event.payload as Record<string, unknown>);
        } else if (event.type === "step_started") {
          const p = event.payload;
          this.lastStepStarted = { stepId: p.step_id, stepTitle: p.step_title, stepIndex: p.step_index, totalSteps: p.total_steps };
          // Reset seenStepIds when a different task's stream begins.
          if (this.stepTrackingTaskId !== taskId) {
            this.stepTrackingTaskId = taskId;
            this.seenStepIds.clear();
          }
          this.seenStepIds.add(p.step_id);
          this.ui.updateWorkbar({ stepIndex: p.step_index, totalSteps: p.total_steps, stepTitle: p.step_title });
        } else if (event.type === "patch_applied") {
          const files = event.payload.touched_files?.join(", ") ?? "";
          this.ui.appendChatThinkingEntry(`patch applied${files ? ` (${files})` : ""}`);
        } else if (event.type === "patch_failed") {
          const err = (event.payload["error"] as string) ?? "";
          this.lastPatchError = err;
          this.ui.appendChatThinkingEntry(`✗ patch failed: ${err.slice(0, 200)}`);
        } else if (event.type === "revision_needed") {
          this.noteDeviation(taskId, "Delta replan triggered: " + ((event.payload["reason"] as string) ?? ""));
          this.ui.appendChatThinkingEntry("revision needed — delta replanning…");
        } else if (event.type === "planning_complete") {
          this.ui.appendChatThinkingEntry(`delta replan complete (${event.payload.confidence} confidence)`);
          this.ui.updateWorkbar(null);
        } else if (event.type === "operation_success") {
          this.ui.appendChatMessage({
            role: "agent",
            content: `✓ ${event.payload.op_type}: ${event.payload.path}`,
            type: "text",
            timestamp: this.now(),
            metadata: {},
          });
        } else if (event.type === "operation_error") {
          this.ui.appendChatMessage({
            role: "agent",
            content: `✗ ${event.payload.op_type}: ${event.payload.path} — ${event.payload.error}`,
            type: "text",
            timestamp: this.now(),
            metadata: {},
          });
        } else if (event.type === "chat_breadcrumb") {
          // Durable transcript record of a resolved gate/plan action, pushed live so it
          // lands in history immediately instead of only on the next thread reload.
          this.ui.appendChatMessage({
            role: "agent",
            content: event.payload.text,
            type: "text",
            taskId: event.payload.task_id,
            timestamp: this.now(),
            metadata: event.payload.edit_outcome === true
              ? { taskId: event.payload.task_id, breadcrumb: true, edit_outcome: true }
              : { taskId: event.payload.task_id, breadcrumb: true },
          });
          // Capture deviation breadcrumbs for run context.
          // COUPLING: prefixes mirror backend breadcrumb constants in
          // services/agentd-py/agentd/orchestrator/engine.py (write_chat_breadcrumb call
          // sites). A reword there silently drops deviation capture — keep in sync.
          const breadcrumbText: string = event.payload.text ?? "";
          if (
            breadcrumbText.startsWith("✓ Scope extension approved") ||
            breadcrumbText.startsWith("✓ Command approved") ||
            breadcrumbText.startsWith("↩ Step changes discarded") ||
            breadcrumbText.startsWith("✓ Validation accepted")
          ) {
            this.noteDeviation(event.payload.task_id ?? taskId, breadcrumbText);
          }
        } else if (event.type === "chat_progress") {
          this.ui.appendChatMessage({
            role: "agent",
            content: event.payload.note,
            type: "text",
            timestamp: this.now(),
            metadata: { progress: true },
          });
        } else if (event.type === "plan_card") {
          // Read-only transcript record (e.g. a feedback-regenerated version picked up
          // when execution starts). Display-only; chat.js dedups by task+content.
          this.ui.appendChatMessage({
            role: "agent",
            content: event.payload.plan_markdown,
            type: "plan_card",
            taskId: event.payload.task_id,
            timestamp: this.now(),
            metadata: { taskId: event.payload.task_id, plan_markdown: event.payload.plan_markdown },
          });
        } else if (event.type === "scope_extension_requested") {
          // Class-A gates render from /live in the pinned slot; poke for an instant update.
          this.forwardGateWait("scope");
        } else if (event.type === "validation_decision_requested") {
          this.forwardGateWait("validation");
        } else if (event.type === "command_approval_requested") {
          this.forwardGateWait("command");
        } else if (event.type === "mcp_approval_requested") {
          this.forwardGateWait("mcp_tool");
        } else if (
          event.type === "env_profile_building" ||
          event.type === "env_profile_built" ||
          event.type === "env_install_running" ||
          event.type === "env_install_done"
        ) {
          this.forwardEnvEvent({ type: event.type, payload: event.payload as Record<string, unknown> });
        } else if (event.type === "done") {
          const status = (event.payload["status"] as string | undefined) ?? "";
          this.ui.hideChatThinking();
          this.ui.updateWorkbar(null);
          if (status === "READY_FOR_REVIEW") {
            this.ui.appendChatMessage({
              role: "agent",
              content: "Execution complete — review the diff in the Tasks panel.",
              type: "text",
              timestamp: this.now(),
              metadata: { task_id: taskId },
            });
          } else if (status === "FAILED" || status === "ABORTED") {
            this.ui.appendChatMessage({
              role: "agent",
              content: `Execution ${status.toLowerCase()} — check the Tasks panel for details.`,
              type: "text",
              timestamp: this.now(),
              metadata: { task_id: taskId },
            });
          }
          break;
        }
      }
    } catch (error) {
      this.ui.showError(`Stream error: ${formatError(error)}`);
    } finally {
      this.openToolEvent = {}; // clear cross-turn stale ids
      this.ui.updateWorkbar(null);
      this.ui.hideChatThinking();
      this.ui.setChatInputEnabled(true);
    }
  }

  async acceptStep(taskId: string): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    const client = this.clientForChat();
    try {
      await client.sendStepDecision(this.liveTaskIdOr(taskId), "accept");
    } catch (error) {
      if (this.isBenignConflict(error)) return;
      this.ui.showError(`Failed to accept step: ${error instanceof Error ? error.message : String(error)}`);
    }
  
});
}

  async discardStep(taskId: string): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    const client = this.clientForChat();
    try {
      await client.sendStepDecision(this.liveTaskIdOr(taskId), "discard");
    } catch (error) {
      if (this.isBenignConflict(error)) return;
      this.ui.showError(`Failed to discard step: ${error instanceof Error ? error.message : String(error)}`);
    }
  
});
}

  private gateReceiptMatches(identity: GateDecisionIdentity, receipt: GateDecisionReceipt): boolean {
    return (Object.keys(identity) as (keyof GateDecisionIdentity)[]).every(key => identity[key] === receipt[key]);
  }

  /** New cards use an exact JSON acceptance, never the legacy streamed endpoints. */
  async handleExactGateDecision(action: GateAction, onAccepted: (isCurrent: () => boolean) => Promise<void>): Promise<void> {

await this.runProjectWork(action.threadId, action.kind, action.gateId, async (verifyWork, isCurrentWork) => {


    const kind = action.kind === "feedback" ? "mode" : action.kind;
    const retry = () => this.ui.retryDecision?.(action.threadId, kind, action.gateId);
    if (this.disposed || action.threadId !== this.activeThreadId || !this.historyActionReady()
        || this.pendingAdmissions.size || this.turnOperation || !this.historyProcess) { retry(); return; }
    const client = this.clientForChat(), process = this.historyProcess, workspace = this.ui.getWorkspacePath();
    if (!client.claimChatGate || !client.getGateDecisionReceipt || !client.getChatActivity) { retry(); this.ui.showWarning("Reconnect to a backend with exact gate decisions before continuing."); return; }
    // Reservation precedes approval, confirmation, network and digest awaits.
    const owner = this.beginTurnOperation(action.threadId, true), mutation = this.historyMutation;
    const current = () => !this.disposed && !owner.retired && this.turnOperation === owner
      && action.threadId === this.activeThreadId && this.settings.getBackendBaseUrl() === owner.url
      && this.ui.getWorkspacePath() === workspace && this.contextState.current(owner.epoch, owner.url);
    const fresh = () => current() && this.historyActionReady() && process === this.historyProcess && mutation === this.historyMutation;
    let identity: GateDecisionIdentity | undefined, accepted = false;
    const acknowledge = async () => {
      if (accepted) return; accepted = true;
      // An acceptance cannot become retryable if local persistence fails afterwards.
      try { await onAccepted(current); }
      catch (error) { this.ui.showWarning(`Decision accepted; local preference could not be saved: ${formatError(error)}`); }
    };
    const readAbort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let postTimer: ReturnType<typeof setTimeout> | undefined;
    try {
      const expected = await this.approvalReady();
      if (!fresh() || expected !== process || !this.approval?.current().confirmed) throw new Error("Approval or conversation changed before the decision.");
      if (!(await this.approval.confirmFirstSend()) || !fresh()) throw new Error("Decision cancelled or history no longer current.");
      const started = Date.now();
      timer = setTimeout(() => readAbort.abort(), 4000);
      const [activity, live] = await Promise.all([
        client.getChatActivity(this.ui.getWorkspacePath() ?? "", readAbort.signal),
        client.getThreadLiveState(action.threadId, readAbort.signal),
      ]);
      const gate = live.pendingGates.find(g => g.gateId === action.gateId && g.kind === kind && !g.agent && !g.team);
      if (!fresh() || readAbort.signal.aborted || Date.now() - started >= 4000 || !activity.supported
          || activity.processId !== process || live.processId !== process || live.gateClaimVersion !== 1
          || !live.approval?.supported || !live.approval.owned || live.approval.processId !== process || !gate
          || !activity.threads.some(t => t.threadId === action.threadId && !t.turnActive)
          || activity.threads.some(t => t.threadId !== action.threadId && (t.turnActive || t.agentsRunning || t.sessionsRunning))) {
        throw new Error("Refresh the current gate and project activity before deciding.");
      }
      const claim: GateDecisionClaim = { version: 1, requestId: randomUUID(), processId: process,
        workspacePath: activity.workspacePath, gateId: action.gateId, kind: action.kind, value: action.value };
      identity = await gateDecisionIdentity(action.threadId, claim);
      if (!fresh() || readAbort.signal.aborted) throw new Error("The conversation changed before dispatch.");
      postTimer = setTimeout(() => owner.abort.abort(), 15_000);
      const receipt = await client.claimChatGate(action.threadId, claim, { signal: owner.abort.signal });
      if (!this.gateReceiptMatches(identity, receipt)) throw new ChatAdmissionUnknownError(new Error("Receipt identity mismatch"));
      await acknowledge(); this.admitTurn(owner); this.finishTurn(owner);
      if (receipt.status === "launch_failed") this.ui.showWarning("The decision was accepted, but its work could not start. It has not been resent.");
    } catch (error) {
      if (identity && error instanceof ChatAdmissionUnknownError && !this.disposed) {
        owner.unknown = { client, gateIdentity: identity, submission: [structuredClone(action)], accepted: acknowledge, checking: false };
        this.pendingAdmissions.add(owner); this.publishAdmission();
        this.ui.showWarning("Decision receipt is unknown. Use Check receipt; do not submit it again."); return;
      }
      this.finishTurn(owner);
      if (current() || this.activeThreadId === action.threadId && this.contextState.current(owner.epoch, owner.url)) {
        retry(); this.ui.showWarning(`Decision not sent: ${formatError(error)}`);
      }
    } finally { clearTimeout(timer); clearTimeout(postTimer); readAbort.abort(); }
  
});

}

  /**
   * Resolve the controller mode gate (Phase F2). A STREAMED dispatch: edit/explain
   * re-enter the loop and create_task hands off, all producing live chat events —
   * so consume it through streamTurn exactly like a normal message turn.
   */
  async handleModeDecisionFromChat(threadId: string, mode: string): Promise<void> {

await this.runProjectWork(threadId, "mode", undefined, async (verifyWork, isCurrentWork) => {


    if (this.disposed || threadId !== this.activeThreadId || this.pendingAdmissions.size) return;
    let owner: TurnOperation | undefined;
    const epoch = this.contextView();
    const mutation = this.historyMutation, process = this.historyProcess;
    const current = () => !this.disposed && this.contextState.current(epoch, this.settings.getBackendBaseUrl()) && threadId === this.activeThreadId;
    try {
      const expectedProcessId = await this.approvalReady();
      if (!current() || this.pendingAdmissions.size || !this.historyActionReady() || mutation !== this.historyMutation || process !== this.historyProcess) return;
      if (Buffer.byteLength(mode, "utf8") > 64 * 1024 * 1024) {
        this.ui.showWarning("This decision exceeds the 64 MiB in-memory submission limit.");
        return;
      }
      await verifyWork(); if (!isCurrentWork()) return;
      this.ui.setChatInputEnabled(false);
      owner = this.beginTurnOperation(threadId, true);
      const admittedOwner = owner;
      const options = { signal: owner.abort.signal, onAdmitted: () => this.admitTurn(admittedOwner) };
      const client = this.clientForChat();
      await this.streamTurn(client.postModeDecision(threadId, mode, expectedProcessId, options), owner);
    } catch (error) {
      if (this.disposed) { if (owner) this.finishTurn(owner); return; }
      if (owner && !owner.admitted && error instanceof ChatAdmissionUnknownError) {
        owner.unknown = { client: this.createClient(owner.url), submission: ["mode", mode], checking: false };
        this.pendingAdmissions.add(owner);
        this.publishAdmission();
        if (current()) {
          this.ui.showWarning("Decision receipt is unknown. Do not submit it again; refresh authoritative live state.");
          this.ui.setChatInputEnabled(false);
        }
        return;
      }
      if (owner) this.finishTurn(owner);
      if (!current()) return;
      if (owner?.admitted) {
        this.ui.showWarning("Decision accepted; refresh to recover its interrupted response.");
        this.ui.setChatInputEnabled(true);
        this.lastLiveSignature = null;
        void this.pollThreadLiveState();
        return;
      }
      this.ui.retryDecision?.(threadId, "mode");
      this.ui.setChatInputEnabled(true);
      if (await this.recoverApproval(error) || !current()) return;
      this.ui.showError(`Failed to send the decision: ${formatError(error)}`);
    }
  
});

}

  /**
   * Resolve the controller clarify gate. A STREAMED dispatch: the answer re-enters the
   * loop (EDIT if the clarify fired mid-edit, else DECIDE), producing live chat events —
   * consume via streamTurn like a normal turn (mirror of handleModeDecisionFromChat).
   */
  async handleClarifyDecisionFromChat(threadId: string, answer: string): Promise<void> {

await this.runProjectWork(threadId, "clarify", undefined, async (verifyWork, isCurrentWork) => {


    if (this.disposed || threadId !== this.activeThreadId || this.pendingAdmissions.size) return;
    let owner: TurnOperation | undefined;
    const epoch = this.contextView();
    const mutation = this.historyMutation, process = this.historyProcess;
    const current = () => !this.disposed && this.contextState.current(epoch, this.settings.getBackendBaseUrl()) && threadId === this.activeThreadId;
    try {
      const expectedProcessId = await this.approvalReady();
      if (!current() || this.pendingAdmissions.size || !this.historyActionReady() || mutation !== this.historyMutation || process !== this.historyProcess) return;
      if (Buffer.byteLength(answer, "utf8") > 64 * 1024 * 1024) {
        this.ui.showWarning("This decision exceeds the 64 MiB in-memory submission limit.");
        return;
      }
      await verifyWork(); if (!isCurrentWork()) return;
      this.ui.setChatInputEnabled(false);
      owner = this.beginTurnOperation(threadId, true);
      const admittedOwner = owner;
      const options = { signal: owner.abort.signal, onAdmitted: () => this.admitTurn(admittedOwner) };
      const client = this.clientForChat();
      await this.streamTurn(client.postClarifyDecision(threadId, answer, expectedProcessId, options), owner);
    } catch (error) {
      if (this.disposed) { if (owner) this.finishTurn(owner); return; }
      if (owner && !owner.admitted && error instanceof ChatAdmissionUnknownError) {
        owner.unknown = { client: this.createClient(owner.url), submission: ["clarify", answer], checking: false };
        this.pendingAdmissions.add(owner);
        this.publishAdmission();
        if (current()) {
          this.ui.showWarning("Decision receipt is unknown. Do not submit it again; refresh authoritative live state.");
          this.ui.setChatInputEnabled(false);
        }
        return;
      }
      if (owner) this.finishTurn(owner);
      if (!current()) return;
      if (owner?.admitted) {
        this.ui.showWarning("Decision accepted; refresh to recover its interrupted response.");
        this.ui.setChatInputEnabled(true);
        this.lastLiveSignature = null;
        void this.pollThreadLiveState();
        return;
      }
      this.ui.retryDecision?.(threadId, "clarify");
      this.ui.setChatInputEnabled(true);
      if (await this.recoverApproval(error) || !current()) return;
      this.ui.showError(`Failed to send the decision: ${formatError(error)}`);
    }
  
});

}

  /**
   * Resolve the controller per-edit review gate (Phase F3). A plain POST — the
   * loop continuation rides the already-open message SSE stream (still consumed by
   * the in-flight streamTurn), so there is nothing to consume here.
   */
  async handleEditDecisionFromChat(
    threadId: string,
    decision: "accept" | "reject",
    reason: string,
    gateId: string,
  ): Promise<void> {

await this.runProjectWork(threadId, "edit", gateId, async (verifyWork, isCurrentWork) => {


    const client = this.clientForChat();
    try {
      await client.postEditDecision(threadId, decision, reason, gateId || undefined);
    } catch (error) {
      if (this.isBenignGateMiss(error)) return;
      if (this.holdUnknownContinuation(error, "edit", gateId)) return;
      this.ui.showError(
        `Failed to send edit decision: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  
});

}

  async applyInlineChange(inlineTaskId: string): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    if (!this.historyActionReady()) { this.ui.showWarning("Reconnect saved history before changing files."); return; }
    const client = this.createClient(this.settings.getBackendBaseUrl());
    try {
      await client.applyInlineChange(inlineTaskId);
      this.ui.showInfo("Changes applied to workspace.");
      this.ui.resolveInlineChangeCard(inlineTaskId, "applied");
    } catch (error) {
      this.ui.showError(`Failed to apply inline change: ${formatError(error)}`);
    }
  
});
}

  async discardInlineChange(inlineTaskId: string): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    if (!this.historyActionReady()) { this.ui.showWarning("Reconnect saved history before changing files."); return; }
    const client = this.createClient(this.settings.getBackendBaseUrl());
    try {
      await client.discardInlineChange(inlineTaskId);
      this.ui.resolveInlineChangeCard(inlineTaskId, "discarded");
    } catch (error) {
      this.ui.showError(`Failed to discard inline change: ${formatError(error)}`);
    }
  
});
}

  async acceptTaskPatch(taskId: string): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    try {
      const result = await this.clientForChat().acceptPatch(taskId);
      this.ui.showInfo("Task finished — changes are in your workspace.");
      // Optimistic copy of the breadcrumb the backend persists on /accept: nothing
      // is listening on the task SSE channel by Finish-time, so without this the
      // durable record only appears after the next webview reload.
      this.appendBreadcrumbMessage(
        taskId,
        `✓ Task finished — ${result.modifiedFiles.length} file(s) applied to the workspace.`,
      );
      // poke so the review card clears sub-interval once status advances
      this.lastLiveSignature = null;
      void this.pollThreadLiveState();
    } catch (error) {
      if (this.isBenignConflict(error)) return;
      this.ui.showError(`Failed to finish task: ${formatError(error)}`);
    }
  
});
}

  async rejectTaskPatch(taskId: string, reason: string): Promise<void> {
    await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    try {
      await this.clientForChat().rejectPatch(taskId, reason.trim() || "closed from chat");
      // Tier B: reject is now a TRUE revert (the backend restores the pre-execution state
      // and deletes task-created files), not "keep changes". Message + breadcrumb mirror that.
      this.ui.showInfo("All changes discarded — workspace rolled back to its pre-task state.");
      this.appendBreadcrumbMessage(
        taskId,
        "✗ All changes discarded — workspace rolled back to its pre-task state.",
      );
      this.lastLiveSignature = null;
      void this.pollThreadLiveState();
    } catch (error) {
      if (this.isBenignConflict(error)) return;
      this.ui.showError(`Failed to discard changes: ${formatError(error)}`);
    }
  
    });
  }

  /** Cooperative Stop for the running task (work-bar). revert rolls the workspace back to
   * its pre-execution state; otherwise the changes applied so far are kept (Tier B). */
  async abortActiveTask(revert: boolean): Promise<void> {
    const taskId = this.latestLiveState?.activeTaskId;
    if (!taskId) return;
    try {
      await this.clientForChat().abortTask(taskId, { revert });
      this.ui.showInfo(
        revert ? "Stopping task and reverting changes…" : "Stopping task; changes so far are kept…",
      );
      this.lastLiveSignature = null;
      void this.pollThreadLiveState();
    } catch (error) {
      if (this.isBenignConflict(error)) return;
      this.ui.showError(`Failed to stop task: ${formatError(error)}`);
    }
  }

  /** Live-mutable "Review each step/edit" preference, sent to BOTH live surfaces.
   *
   * A controller chat turn has no task, so the task route alone (the only call this
   * used to make) meant flipping the checkbox mid-turn reached nothing and the change
   * waited for the next message. The chat route reaches the in-flight turn's control,
   * which the loop re-reads before every edit.
   *
   * A 409 from either is benign — it just means that surface has nothing running, and
   * the value still governs the next task's creation default / the next message. */
  configureApproval(deps: Omit<ApprovalDependencies, "client" | "render">): void {
    this.approval = new ApprovalCoordinator({ ...deps, endpoint: () => this.settings.getBackendBaseUrl(), client: () => this.clientForChat(),
      render: view => this.ui.renderApproval?.(view) });
    this.ui.renderApproval?.(this.approval.current());
  }

  async setApprovalMode(mode: unknown, confirmedFullAccess = false, dontShowAgain = false): Promise<void> {
    if (!isApprovalMode(mode) || !this.approval) return;
    await this.approval.select(mode, confirmedFullAccess, dontShowAgain);
    await this.approval.acknowledgeUserSelection();
    this.lastLiveSignature = null;
    void this.pollThreadLiveState();
  }

  private async approvalReady(): Promise<string | undefined> {
    return this.approval?.ready();
  }

  async setSandboxEnabled(enabled: boolean): Promise<void> {
    if (!this.approval) throw new Error("The sandbox needs the chat controller");
    await this.approval.setSandboxEnabled(enabled);
  }

  async approvalTrustGranted(): Promise<void> {
    await this.approval?.trustGranted();
  }

  private async recoverApproval(error: unknown): Promise<boolean> {
    return await this.approval?.recover(error) ?? false;
  }

  async setReviewPref(autoAccept: boolean): Promise<void> {
    if (this.approval) { await this.setApprovalMode(autoAccept ? "accept_edits" : "ask"); return; }
    const taskId = this.latestLiveState?.activeTaskId;
    try {
      const result = await this.clientForChat().setGlobalReviewPref({ autoAccept });
      if (result.background > 0) {
        const noun = result.background === 1 ? "edit" : "edits";
        this.ui.showInfo(`Accepted ${result.background} pending background ${noun}.`);
      }
      if (taskId) {
        await this.clientForChat().setReviewPref(taskId, { autoAccept });
      }
      this.lastLiveSignature = null;
      void this.pollThreadLiveState();
    } catch (error) {
      if (this.isBenignConflict(error)) return;
      this.ui.showError(`Failed to update review preference: ${formatError(error)}`);
    }
  }

  /** Spec §5.4: the backend's values must match the toggles after every (re)connect —
   * a restarted backend starts from its defaults. */
  async pushPreferences(prefs: { autoAccept: boolean; planMode: boolean }): Promise<void> {
    try {
      const client = this.clientForChat();
      if (this.approval) await this.approval.connect();
      else if (!prefs.autoAccept) await client.setGlobalReviewPref({ autoAccept: false });
      await client.setPlanMode(prefs.planMode);
    } catch (error) {
      this.ui.showError(`Approval mode not confirmed — retry: ${formatError(error)}`);
    }
  }

  /** Fired when the /live poll succeeds after failing — the host's only periodic
   * contact with an explicitly configured backend, where onBackendReady never fires. */
  onBackendReachable(listener: () => void): void {
    this.backendReachableListeners.push(listener);
  }

  /** Mirror of the backend's persisted breadcrumb, rendered immediately. The
   *  persisted copy takes over on the next thread reload (full list replace —
   *  no duplication). */
  private appendBreadcrumbMessage(taskId: string, text: string): void {
    this.ui.appendChatMessage({
      role: "agent",
      content: text,
      type: "text",
      taskId,
      timestamp: this.now(),
      metadata: { taskId, breadcrumb: true },
    });
  }

  async resumeTaskById(taskId: string, stage: "plan" | "execute"): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    let childId: string;
    try {
      const response = await this.clientForChat().resumeTask(taskId, { stage });
      childId = response.taskId;
      this.ui.showInfo(`Resumed as ${childId}`);
    } catch (error) {
      this.ui.showError(`Failed to resume: ${formatError(error)}`);
      return;
    }
    // the child task is a fresh run — never let the parent's step/error history describe it
    this.seenStepIds.clear();
    this.stepTrackingTaskId = null;
    this.lastStepStarted = null;
    this.lastPatchError = null;
    this.runDeviations = [];
    this.deviationsTaskId = null;
    // Optimistic task_card anchor for the child, mirroring the backend record the
    // resume route now persists (the persisted copy replaces this on the next reload —
    // same optimistic pattern as appendBreadcrumbMessage). Gives the resumed run a
    // transcript row instead of appearing only as live pills.
    this.ui.appendChatMessage({
      role: "agent",
      content: childId,
      type: "task_card",
      taskId: childId,
      timestamp: this.now(),
      metadata: {},
    });
    // Force the next poll to re-render against the child task (signature reset).
    this.lastLiveSignature = null;
    void this.pollThreadLiveState();
    // An execute-stage resume runs execution immediately on the child's task channel.
    // Nobody else subscribes to a resumed child's stream, so without this the run is
    // mute — no tool pills, no work bar, no step progress — until a reload. Attach to
    // it through the same render path as a post-approval implement. A plan-stage resume
    // re-plans and pauses at AWAITING_PLAN_APPROVAL (no terminal event to end the
    // stream); it is driven by /live + the live plan card's Implement path instead.
    if (stage === "execute") {
      await this.streamTaskIntoChatThread(childId, "Resuming execution…");
    }
  
});
}

  /** Retire only this view attachment. The server owns accepted background work. */
  canDetachProjectView(): boolean { return !this.workReservation && this.pendingAdmissions.size === 0; }
  detachProjectView(): void {
    if (!this.canDetachProjectView()) throw new Error("Finish the pending project action before switching projects.");
    this.contextView(true);
    this.stopPolling(); this.stopStream(); this.stopLiveStatePolling();
    if (this.historyTimer) clearInterval(this.historyTimer); this.historyTimer=null;
    this.agentViews.closeAll(); this.teamViews.closeAll();
  }

  dispose(): void {
    this.disposed = true;
    for (const owner of this.pendingAdmissions) {
      owner.retired = true;
      delete owner.unknown;
      owner.abort.abort();
    }
    this.pendingAdmissions.clear();
    this.turnOperation?.abort.abort();
    // Teardown must work before Setup has supplied a backend URL. Do not acquire
    // configuration or render a fresh context while retiring this attachment.
    this.contextState.navigate(null, "");
    this.streamEpoch++;
    const errors: unknown[] = [];
    for (const clean of [
      () => this.stopPolling(), () => this.stopStream(), () => this.stopLiveStatePolling(),
      () => this.workAuthority.dispose(), () => this.historyReader.dispose(),
      () => this.historyCache.clear(), () => this.historyEchoes.clear(),
      () => { if (this.historyTimer) clearInterval(this.historyTimer); this.historyTimer = null; },
      () => this.agentViews.closeAll(), () => this.teamViews.closeAll(),
    ]) { try { clean(); } catch (error) { errors.push(error); } }
    if (errors.length) throw new AggregateError(errors, "Some conversation resources could not be closed.");
  }

  async stopActiveTurn(): Promise<void> {
    // Detached turns are not cancelled by disconnecting the SSE anymore — Stop is an
    // explicit POST /stop (a slimmer cousin of task /abort). Still abort the local SSE
    // reader so the relay loop unwinds promptly; the server-side cancel is the real stop.
    const epoch = this.contextView();
    this.turnAbort?.abort();
    const threadId = this.activeThreadId;
    if (!threadId) return;
    const current = () => this.activeThreadId === threadId && this.contextState.current(epoch, this.settings.getBackendBaseUrl());
    try {
      const result = await this.clientForChat().stopChatTurn(threadId);
      // We aborted our SSE reader above, so the backend's live "✗ Stopped" breadcrumb
      // broadcast never reaches the open webview (finding 7) — only its durable copy
      // survives (shows on reopen). Render it optimistically, like accept/discard; a
      // reopen replaces it with the durable copy (no dup). Gated on ok = a real stop.
      if (current() && result.ok) this.appendBreadcrumbMessage("", "✗ Stopped");
    } catch (error) {
      if (!current() || this.isBenignConflict(error)) return;
      // A failed stop is non-fatal — the turn finishes on its own; log only.
      this.ui.showWarning(`Stop failed: ${formatError(error)}`);
    } finally {
      if (current()) { this.lastLiveSignature = null; void this.pollThreadLiveState(); }
    }
  }

  private forwardToolCall(source: "explore" | "execution" | "planning", payload: Record<string, unknown>): void {
    // Prefer the backend's call_index as the pill id (controller execution pills): it
    // equals the persisted pill id, so a switch-back resume dedups replayed pills against
    // the loaded in-flight message (useAppState appendToolEvent). Other sources keep the
    // session-monotonic seq.
    const id = typeof payload["call_index"] === "number"
      ? (payload["call_index"] as number)
      : ++this.toolEventSeq;
    this.openToolEvent[source] = id;
    const thought = (payload["thought"] as string) || undefined;
    this.ui.appendToolEvent({
      id,
      tool: (payload["tool"] as string) ?? "",
      args: (payload["args"] as Record<string, unknown>) ?? {},
      ...(thought !== undefined ? { thought } : {}),
      source,
    });
  }

  private forwardToolResult(source: "explore" | "execution" | "planning", payload: Record<string, unknown>): void {
    const id = typeof payload["call_index"] === "number"
      ? payload["call_index"] as number
      : this.openToolEvent[source];
    if (id === undefined) return; // result without a call (replay edge) — drop
    if (this.openToolEvent[source] === id) delete this.openToolEvent[source];
    const output = (payload["output"] as string) ?? "";
    const note = payload["approval_note"];
    const marker = payload["sandbox"];
    if (marker === "blocked" || marker === "outside") {
      this.ui.appendToolResult(id, output, payload["is_error"] === true, typeof note === "string" && note ? note : undefined, marker);
    } else if (typeof note === "string" && note) {
      this.ui.appendToolResult(id, output, payload["is_error"] === true, note);
    } else {
      this.ui.appendToolResult(id, output, payload["is_error"] === true);
    }
  }

  /** Test/diagnostic surface — exposes deviation breadcrumbs captured during the last stream. */
  get observedDeviations(): readonly string[] {
    return this.runDeviations;
  }

  private noteDeviation(taskId: string, text: string): void {
    if (this.deviationsTaskId !== taskId) { this.deviationsTaskId = taskId; this.runDeviations = []; }
    this.runDeviations.push(text);
  }

  /**
   * Forward one of the four env lifecycle events (env_profile_building / env_profile_built /
   * env_install_running / env_install_done) as a thinking entry + workbar phase label/clear.
   * Both SSE loops call this to keep behaviour in sync.
   */
  private forwardEnvEvent(event: { type: string; payload: Record<string, unknown> }): void {
    if (event.type === "env_profile_building") {
      this.ui.appendChatThinkingEntry("Preparing workspace env profile…");
      this.ui.updateWorkbar({ phaseLabel: "Profiling workspace environment…" });
    } else if (event.type === "env_profile_built") {
      const count = (event.payload["ecosystems_count"] as number) ?? 0;
      const bootstrap = (event.payload["bootstrap_needed"] as boolean) ?? false;
      this.ui.appendChatThinkingEntry(
        bootstrap
          ? "Env profile: workspace has no manifests yet (bootstrap_needed)"
          : `Env profile ready (${count} ecosystem${count === 1 ? "" : "s"})`,
      );
      this.ui.updateWorkbar(null);
    } else if (event.type === "env_install_running") {
      const cmd = (event.payload["command"] as string) ?? "";
      const scope = (event.payload["scope_key"] as string) ?? "";
      this.ui.appendChatThinkingEntry(`Syncing deps: ${cmd} (${scope})`);
      this.ui.updateWorkbar({ phaseLabel: `Syncing dependencies: ${cmd}…` });
    } else if (event.type === "env_install_done") {
      const ok = (event.payload["exit_ok"] as boolean) ?? false;
      const scope = (event.payload["scope_key"] as string) ?? "";
      this.ui.appendChatThinkingEntry(
        ok ? `Deps synced for ${scope}` : `Deps sync FAILED for ${scope}`,
      );
      this.ui.updateWorkbar(null);
    }
  }

  /**
   * Forward a gate-wait event: append the matching "Waiting for … approval/decision…"
   * thinking entry AND poke a /live poll so the pinned gate card renders instantly.
   * Both SSE loops call this — reconciles a pre-existing divergence where
   * streamTaskIntoChatThread's validation/command cases were poke-only.
   */
  private forwardGateWait(kind: "scope" | "validation" | "command" | "mcp_tool"): void {
    const label =
      kind === "scope" ? "Waiting for scope approval…"
      : kind === "validation" ? "Waiting for validation decision…"
      : kind === "mcp_tool" ? "Waiting for MCP tool approval…"
      : "Waiting for command approval…";
    this.ui.appendChatThinkingEntry(label);
    void this.pollThreadLiveState();
  }

  private startStream(taskId: string): void {
    if (this.streamController) return;
    this.streamController = new AbortController();
    const { signal } = this.streamController;
    const client = this.clientForSession();
    client
      .streamPatch(taskId, (event) => {
        if (event.type === "planning_tool_call") {
          const tool = (event.payload as Record<string, unknown>)["tool"] as string ?? "";
          const action = tool === "read_file" ? "Reading file"
            : tool === "list_directory" ? "Listing directory"
            : tool === "search_code" ? "Searching codebase"
            : tool === "search_semantic" ? "Semantic search"
            : tool === "search_index" ? "Finding code candidates"
            : tool;
          this.ui.updateChatThinking(`Replanning: ${action}…`);
        } else if (event.type === "planning_complete") {
          void this.clientForSession().getTask(taskId).then((task) => {
            if (task.planMarkdown) {
              this.ui.appendChatMessage({
                role: "agent",
                content: task.planMarkdown,
                type: "plan_card",
                taskId,
                timestamp: this.now(),
                metadata: { taskId, plan_markdown: task.planMarkdown },
              });
            }
            this.ui.hideChatThinking();
            this.ui.setChatInputEnabled(true);
          });
        } else if (event.type === "scope_extension_requested") {
          // Fire and forget — prompt the user; on response, post the decision.
          void this.handleScopeExtensionRequest(taskId, event);
        }
        // step_review_requested: step review surfaces via the /live poll (renderLiveGates kind "step").
      }, signal)
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        this.ui.showWarning(`Patch stream error: ${err instanceof Error ? err.message : String(err)}`);
      })
      .finally(() => {
        this.streamController = null;
      });
  }

  async handleScopeDecisionFromChat(
    taskId: string,
    files: string[],
    decision: "approve" | "reject",
    remember: boolean
  ): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    try {
      await this.clientForChat().sendScopeDecision(
        this.liveTaskIdOr(taskId),
        { decision, files: decision === "approve" ? files : [], remember }
      );
    } catch (err) {
      if (this.isBenignConflict(err)) return;
      this.ui.showError(`Failed to send scope decision: ${err instanceof Error ? err.message : String(err)}`);
    }
  
});
}

  async handleValidationDecisionFromChat(
    taskId: string,
    decision: "accept" | "reject"
  ): Promise<void> {
await this.runProjectWork(this.activeThreadId ?? "", "task", undefined, async (verifyWork, isCurrentWork) => {

    try {
      await this.clientForChat().sendValidationDecision(this.liveTaskIdOr(taskId), decision);
    } catch (err) {
      if (this.isBenignConflict(err)) return;
      this.ui.showError(`Failed to send validation decision: ${err instanceof Error ? err.message : String(err)}`);
    }
  
});
}

  async handleCommandDecisionFromChat(
    taskId: string,
    decision: CommandDecision,
    gateId: string,
  ): Promise<void> {

await this.runProjectWork(taskId, "command", gateId, async (verifyWork, isCurrentWork) => {


    try {
      // Task gates carry "task:{taskId}:command" ids and resolve on the task route;
      // every other command gate is a controller gate on the thread (taskId carries the
      // thread id for those — see pollThreadLiveState).
      if (gateId.startsWith("task:")) {
        await this.clientForChat().sendCommandDecision(this.liveTaskIdOr(taskId), decision);
        return;
      }
      await this.clientForChat().postChatCommandDecision(taskId, decision, gateId || undefined);
    } catch (err) {
      if (this.isBenignGateMiss(err)) return;
      if (this.holdUnknownContinuation(err, "command", gateId)) return;
      this.ui.showError(`Failed to send command decision: ${err instanceof Error ? err.message : String(err)}`);
    }
  
});

}

  async handleMcpDecisionFromChat(
    threadId: string,
    decision: McpToolDecision,
    gateId: string,
  ): Promise<void> {

await this.runProjectWork(threadId, "mcp_tool", gateId, async (verifyWork, isCurrentWork) => {


    try {
      // mcp_tool gates are controller-only (no task path) — always the chat route.
      await this.clientForChat().postChatMcpDecision(threadId, decision, gateId || undefined);
    } catch (err) {
      if (this.isBenignGateMiss(err)) return;
      if (this.holdUnknownContinuation(err, "mcp_tool", gateId)) return;
      this.ui.showError(
        `Failed to send MCP decision: ${err instanceof Error ? err.message : String(err)}`
      );
    }
  
});

}

  private async handleScopeExtensionRequest(
    taskId: string,
    event: Extract<StreamEvent, { type: "scope_extension_requested" }>
  ): Promise<void> {
    const result = await this.ui.promptForScopeDecision({
      files: event.payload.files,
      reason: event.payload.reason,
      stepId: event.payload.step_id
    });
    if (!result) return; // user dismissed — task stays paused until timeout

    const client = this.clientForChat();
    try {
      await client.sendScopeDecision(this.liveTaskIdOr(taskId), {
        decision: result.decision,
        files: result.decision === "approve" ? event.payload.files : [],
        remember: result.remember
      });
    } catch (err) {
      if (this.isBenignConflict(err)) return;
      this.ui.showError(
        `Failed to send scope decision: ${err instanceof Error ? err.message : String(err)}`
      );
    }
  }

  private stopStream(): void {
    this.streamController?.abort();
    this.streamController = null;
  }

  private syncStream(status: TaskStatus, taskId: string): void {
    if (
      status === "QUEUED" ||
      status === "CONTEXT_READY" ||
      status === "PLANNED" ||
      status === "EXECUTING" ||
      status === "REPAIRING"
    ) {
      this.startStream(taskId);
    } else {
      this.stopStream();
    }
  }

  private async pullLatestTask(): Promise<void> {
    if (!this.session) {
      return;
    }

    const client = this.clientForSession();
    let task: TaskView;
    try {
      task = await client.getTask(this.session.taskId);
    } catch (error) {
      this.ui.showWarning(`Failed to refresh task state: ${formatError(error)}`);
      return;
    }

    this.latestTask = task;
    this.session = {
      ...this.session,
      status: task.status,
      updatedAt: this.now(),
    };
    await this.sessionStore.save(this.session);

    this.syncStream(task.status, task.taskId);

    if (shouldStopPolling(task.status)) {
      this.stopPolling();
    }

    if (shouldLoadResult(task.status)) {
      try {
        this.latestResult = await client.getTaskResult(this.session.taskId);
      } catch (error) {
        this.ui.showWarning(`Task result unavailable: ${formatError(error)}`);
      }
    }

    if (TERMINAL_STATUSES.has(task.status)) {
      this.ui.showInfo(`Task ${task.taskId} is ${task.status}.`);
    }
  }

  private startPolling(): void {
    this.stopPolling();

    if (!this.session) {
      return;
    }

    this.poller = new TaskPoller({
      intervalMs: this.settings.getPollIntervalMs(),
      poll: async () => this.clientForSession().getTask(this.session!.taskId),
      onUpdate: async (task) => {
        this.latestTask = task;
        this.session = {
          ...this.session!,
          status: task.status,
          updatedAt: this.now(),
        };
        await this.sessionStore.save(this.session);

        this.syncStream(task.status, task.taskId);

        if (shouldLoadResult(task.status)) {
          try {
            this.latestResult = await this.clientForSession().getTaskResult(this.session.taskId);
          } catch {
            // keep latest known result when retrieval fails
          }
        }
      },
      onError: (error) => {
        this.ui.showWarning(`Polling failed: ${formatError(error)}`);
      },
    });

    this.poller.start();
  }

  private stopPolling(): void {
    this.poller?.stop();
    this.poller = null;
  }

  private clientForChat(): BackendTaskClient {
    return this.createClient(this.settings.getBackendBaseUrl());
  }

  /**
   * The task id an action should target right now. Resume churns the task id
   * (parent→child); the card may have been rendered against an older id, so we
   * prefer the live state's current active_task_id and fall back to the card's.
   */
  private liveTaskIdOr(fallback: string): string {
    return this.latestLiveState?.activeTaskId ?? fallback;
  }

  /**
   * A 409 from a decision POST means the gate already moved on (resolved by a newer
   * poll, a timeout, or another client). It's benign — the next /live poll reconciles
   * the card — so swallow it rather than alarming the user with an error toast.
   */
  private isBenignConflict(err: unknown): boolean {
    return (
      typeof err === "object" &&
      err !== null &&
      (err as { status?: number }).status === 409
    );
  }
  /**
   * Gate-addressed decisions (edit/command/MCP on the chat routes) also answer 404 when
   * the named gate is no longer pending — an auto-accept, a stop, or another click got
   * there first. Same reconciliation as a 409: the next /live poll redraws the cards.
   * Kept separate from isBenignConflict so a 404 from any other route still surfaces.
   */
  private isBenignGateMiss(err: unknown): boolean {
    return (
      this.isBenignConflict(err) ||
      (typeof err === "object" && err !== null && (err as { status?: number }).status === 404)
    );
  }


  /** Start polling the active thread's /live state. Idempotent. */
  private startLiveStatePolling(): void {
    if (this.liveStateTimer) {
      return;
    }
    const intervalMs = Math.max(this.settings.getPollIntervalMs(), 500);
    this.liveStateTimer = setInterval(() => {
      void this.pollThreadLiveState();
    }, intervalMs);
    void this.pollThreadLiveState();
  }

  private stopLiveStatePolling(): void {
    if (this.liveStateTimer) {
      clearInterval(this.liveStateTimer);
      this.liveStateTimer = null;
    }
  }

  /**
   * Reconcile the active thread's live cards from persisted backend state.
   * Renders exactly one gate card (per kind) and one plan card, replace-not-append,
   * removed when null. Signature-deduped so an unchanged poll causes no webview churn.
   * This is what makes gate/plan cards reappear after a webview reload.
   */
  async pollThreadLiveState(): Promise<void> {
    if (this.disposed) return;
    const epoch = this.contextView();
    const pollEpoch = this.contextPollEpoch;
    const currentPoll = () => pollEpoch === this.contextPollEpoch && this.contextState.current(epoch, this.settings.getBackendBaseUrl());
    const url = this.settings.getBackendBaseUrl();
    if (this.livePollInFlight) return;
    this.livePollInFlight = true;
    try {
    const threadId = this.activeThreadId;
    if (!threadId) {
      return;
    }
    let live: ThreadLiveState;
    try {
      live = await this.clientForChat().getThreadLiveState(threadId);
    } catch {
      // Transient backend/poll error — keep the last rendered cards; next tick retries.
      if (!currentPoll()) return;
      this.contextState.fail(epoch, url);
      this.renderContext();
      this.livePollFailing = true;
      this.historyLiveAt = 0; this.historyFailed = true; this.clearActionableHistory(); this.publishHistory();
      return;
    }
    if (!currentPoll() || threadId !== this.activeThreadId) return;
    this.contextState.accept(live.contextUsage, epoch, url, "live", live.processId);
    this.renderContext();
    if (this.livePollFailing) {
      this.livePollFailing = false;
      for (const listener of this.backendReachableListeners) listener();
    }
    if (this.historyProcess && live.processId && this.historyProcess !== live.processId) {
      this.contextView(true); this.attachHistory(threadId); return;
    }
    if (!this.historyProcess && live.processId && this.historyEpoch === epoch) {
      this.historyReader.cancel(); this.historyLoaded = false; this.historyNextAt = 0;
    }
    this.historyProcess = live.processId ?? null;
    this.latestLiveState = live;
    void this.refreshWorkStatus(threadId, epoch);
    this.approval?.observe(live.approval, live.processId);
    this.maybeRefreshUsage(threadId, live);

    // Saved-history following is independent of live signature/count. Never replay
    // durable SSE events on reattachment; the replay buffer has no event cursor.
    if (this.historyEpoch === epoch) {
      this.historyLiveAt = Date.now();
      this.publishHistory();
    }
    void this.reconcileTranscript(threadId, live.messageCount,
      this.lastTurnActive && !live.turnActive);

    const signature = JSON.stringify({
      taskId: live.activeTaskId,
      status: live.status,
      // turnActive drives composer enable/disable and is independent of the cards: a
      // controller chat turn has no task, so status/gate/plan stay null for the whole
      // turn. Omitting turnActive here lets the turn-end transition (true→false) get
      // deduped away, so sendLiveStatus(..., false) is never delivered and the composer
      // stays wedged on "Agent is working…". Same dedup-lock bug class as runSummary/
      // narrative/failure below.
      turnActive: live.turnActive,
      gates: live.pendingGates,
      plan: live.plan,
      // Durable telemetry is finalized server-side AFTER the READY_FOR_REVIEW/terminal
      // status save (the narrative is a later LLM call), so it arrives on a poll where
      // status is unchanged. Without these, the dedup locks on the first render and the
      // Review/Error card never picks up the run_summary/narrative until a reload.
      runSummary: live.runSummary,
      narrative: live.taskNarrative,
      failure: live.failureSummary,
      // INVARIANT (see CLAUDE.md /live dedup): the todo checklist is a durable signal
      // consumed after this gate — it MUST be in the signature, or a checklist change
      // (e.g. an item flipped to done) is deduped away and the card never updates.
      todos: live.todos,
      // Same invariant, both directions: sessions MUST be here or the strip never
      // updates; AND the rows must stay stable while nothing real changes (backend
      // ships started_at, never a ticking age_sec/unread_bytes) or this signature
      // would differ on every 1s poll and re-fire the whole render block at 1 Hz.
      sessions: live.sessions,
      // INVARIANT (CLAUDE.md /live dedup): roster rows are consumed after this gate.
      agents: live.agents,
      agentsRunning: live.agentsRunning,
      // INVARIANT (CLAUDE.md /live dedup): team rows are consumed after this gate.
      teams: live.teams,
      // INVARIANT (CLAUDE.md /live dedup): the access card is consumed after this gate.
      providerAccess: live.providerAccess,
      approval: live.approval,
      turnKind: live.turnKind,
      messageCount: live.messageCount,
    });
    if (signature === this.lastLiveSignature) return;
    this.lastLiveSignature = signature;

    if (live.pendingGates.length > 0) {
      this.ui.renderLiveGates(live.pendingGates.map((g) => ({
        gateId: g.gateId,
        kind: g.kind,
        payload: g.payload,
        agent: g.agent,
        team: g.team,
        // Per gate: controller gates have no task (their decisions go to the thread),
        // task gates carry "task:" ids — so a mixed list addresses each correctly.
        taskId: g.gateId.startsWith("task:") ? (live.activeTaskId ?? threadId) : threadId,
      })));
    } else {
      this.ui.clearLiveGates();
    }

    if (live.plan && live.activeTaskId) {
      this.ui.renderLivePlan({
        taskId: live.activeTaskId,
        planMarkdown: String(live.plan["plan_markdown"] ?? ""),
      });
    } else {
      this.ui.clearLivePlan();
    }

    if (live.status === "READY_FOR_REVIEW" && live.activeTaskId) {
      try {
        // Signature invariant: this block reads getTaskResult fields (modifiedFiles,
        // shadowWorkspacePath, plan) that are NOT in the dedup signature. That is safe
        // ONLY because they are immutable once status===READY_FOR_REVIEW — the result is
        // frozen at that terminal-ish state, so a same-status re-poll can't carry a newer
        // result. If a future change lets the result mutate at unchanged status, add a
        // result fingerprint to the signature (same class as the turnActive fix above).
        const result = await this.clientForChat().getTaskResult(live.activeTaskId);
        if (!currentPoll()) return;
        // Tier B: prefer the durable run_summary (survives reload) over extension-observed
        // ephemeral counts; fall back to the ephemeral values for live-feel before it lands.
        const rs = live.runSummary;
        const review = {
          taskId: live.activeTaskId,
          modifiedFiles: result.modifiedFiles,
          shadowWorkspacePath: result.shadowWorkspacePath ?? null,
          stepsCompleted: rs ? rs.stepsCompleted : (this.seenStepIds.size > 0 ? this.seenStepIds.size : null),
          stepsTotal: rs
            ? rs.stepsTotal
            : (result.plan && Array.isArray((result.plan as { steps?: unknown[] }).steps)
              ? ((result.plan as { steps: unknown[] }).steps.length)
              : null),
          deviations: rs
            ? rs.deviations
            : (this.deviationsTaskId === live.activeTaskId ? [...this.runDeviations] : []),
          ...(live.taskNarrative
            ? { narrative: { headline: live.taskNarrative.headline, points: live.taskNarrative.points } }
            : {}),
        };
        this.latestLiveReview = { taskId: review.taskId, shadowWorkspacePath: review.shadowWorkspacePath };
        this.ui.renderLiveReview(review);
      } catch {
        // result not ready yet — reset signature so the next poll retries instead of deduping
        if (!currentPoll()) return;
        this.lastLiveSignature = null;
      }
    } else {
      this.latestLiveReview = null;
      this.ui.clearLiveReview();
    }

    if ((live.status === "FAILED" || live.status === "ABORTED") && live.activeTaskId) {
      // Tier B: prefer the durable failure_summary (survives reload); fall back to the
      // extension-observed ephemeral step/patch detail for live-feel before it lands.
      const fs = live.failureSummary;
      const detailParts: string[] = [];
      if (fs) {
        if (fs.stepIndex != null) detailParts.push(`step ${fs.stepIndex}`);
        detailParts.push(`${fs.errorClass}: ${fs.message}`);
      } else {
        if (this.lastStepStarted) {
          detailParts.push(`${this.lastStepStarted.stepTitle} (step ${this.lastStepStarted.stepIndex} of ${this.lastStepStarted.totalSteps})`);
        }
        if (this.lastPatchError) detailParts.push(this.lastPatchError);
      }
      this.ui.renderLiveError({
        taskId: live.activeTaskId,
        status: live.status,
        ...(detailParts.length ? { detail: detailParts.join(" — ") } : {}),
        ...(live.taskNarrative
          ? { narrative: { headline: live.taskNarrative.headline, points: live.taskNarrative.points } }
          : {}),
      });
    } else {
      this.ui.clearLiveError();
    }

    if (live.todos && live.todos.length > 0) {
      this.ui.renderLiveTodos({ items: live.todos });
    } else {
      this.ui.clearLiveTodos();
    }

    this.ui.renderProviderAccess(live.providerAccess ?? null);

    if (live.sessions && live.sessions.length > 0) {
      this.ui.renderLiveSessions({ items: live.sessions });
    } else {
      this.ui.clearLiveSessions();
    }

    if (live.agents && live.agents.length > 0) {
      this.ui.renderAgents(live.agents);
      for (const agent of live.agents) {
        this.agentViews.noteStatus(agent.agentId, agent.status);
        this.agentViews.noteActivation(agent.agentId, agent.activationCount);
      }
    }
    const liveTeamIds = new Set((live.teams ?? []).map((t) => t.teamId));
    if (live.teams && live.teams.length > 0) this.ui.renderLiveTeams(live.teams);
    for (const team of live.teams ?? []) this.teamViews.noteState(threadId, team.teamId, team);
    // /live lists live teams only: one that left has ended — reload its summary.
    if ([...this.lastLiveTeamIds].some((id) => !liveTeamIds.has(id))) {
      void this.refreshTeams(threadId);
    }
    this.lastLiveTeamIds = liveTeamIds;
    if (this.lastTurnActive && !live.turnActive) {
      void this.refreshAgentRoster(threadId);
      void this.refreshTeams(threadId);
    }
    this.lastTurnActive = live.turnActive ?? false;
    this.ui.sendLiveStatus(live.status ?? null, live.turnActive ?? false, {
      turnKind: live.turnKind, agentsRunning: live.agentsRunning,
    });
    } finally {
      if (currentPoll()) this.livePollInFlight = false;
    }
  }

  /** PTY inspect fetch for the expandable session strip. Read-only (the
   * backend serves it off an independent buffer view — never advances the
   * model's read cursor). Null on any error: the webview shows "unavailable"
   * rather than surfacing a transient 404 (session dropped between polls). */
  async fetchSessionTranscript(sessionId: string): Promise<SessionTranscript | null> {
    const threadId = this.activeThreadId;
    if (!threadId) {
      return null;
    }
    try {
      return await this.clientForChat().getSessionTranscript(threadId, sessionId);
    } catch {
      return null;
    }
  }

  private clientForSession(): BackendTaskClient {
    if (!this.session) {
      throw new Error("No active session");
    }
    return this.createClient(this.session.backendBaseUrl);
  }
}

function isConflictError(error: unknown): boolean {
  return formatError(error).includes("(409 ");
}

function shouldLoadResult(status: TaskStatus): boolean {
  return status === "READY_FOR_REVIEW" || TERMINAL_STATUSES.has(status);
}

function formatError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
