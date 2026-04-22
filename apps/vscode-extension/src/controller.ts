import type {
  BackendTaskClient,
  PatchStreamEvent,
  ResumeTaskResponse,
  TaskResult,
  TaskStatus,
  TaskSubmission,
  TaskView,
} from "@ai-editor/editor-client";

import { buildReviewFileEntries } from "./review-files.js";
import { SessionStore } from "./session-store.js";
import { shouldStopPolling, TaskPoller } from "./task-poller.js";
import type {
  ReviewFileEntry,
  ReviewPanelViewModel,
  TaskMode,
  TaskSessionState,
} from "./types.js";

const TERMINAL_STATUSES: ReadonlySet<TaskStatus> = new Set(["SUCCEEDED", "FAILED", "ABORTED"]);

export interface SettingsProvider {
  getBackendBaseUrl(): string;
  getDefaultMode(): TaskMode;
  getPollIntervalMs(): number;
}

export interface ControllerUI {
  getWorkspacePath(): string | null;
  promptForGoal(): Promise<string | undefined>;
  promptForTaskId(): Promise<string | undefined>;
  promptForRejectReason(): Promise<string | undefined>;
  promptForResumeStage(): Promise<"plan" | "feedback" | "execute" | undefined>;
  promptForMaxIterationsOverride(): Promise<number | undefined>;
  showInfo(message: string): void;
  showWarning(message: string): void;
  showError(message: string): void;
  updatePanel(model: ReviewPanelViewModel): void;
}

export interface DiffService {
  openDiff(entry: ReviewFileEntry): Promise<void>;
}

export type BackendClientFactory = (baseUrl: string) => BackendTaskClient;

export class AiEditorController {
  private session: TaskSessionState | null = null;
  private latestTask: TaskView | null = null;
  private latestResult: TaskResult | null = null;
  private poller: TaskPoller | null = null;
  private streamController: AbortController | null = null;
  private patchEvents: PatchStreamEvent[] = [];

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
      this.pushPanel();
      return;
    }

    this.session = restored;
    this.pushPanel();
    if (!shouldStopPolling(restored.status)) {
      this.startPolling();
    }
    await this.refreshTask();
  }

  async startTask(): Promise<void> {
    const workspacePath = this.ui.getWorkspacePath();
    if (!workspacePath) {
      this.ui.showError("Open a workspace folder before starting an AI Editor task.");
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
    this.patchEvents = [];

    await this.sessionStore.save(this.session);
    this.pushPanel();
    this.startPolling();
    this.startStream(submission.taskId);
    this.ui.showInfo(`Started AI Editor task ${submission.taskId}`);
  }

  openReviewPanel(): void {
    this.pushPanel();
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
    this.patchEvents = [];

    await this.sessionStore.save(this.session);
    this.pushPanel();

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
  }

  async rejectPatch(): Promise<void> {
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
  }

  async providePlanFeedback(feedback: string | null): Promise<void> {
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
      this.patchEvents = [];
      this.startStream(task.taskId);

      this.pushPanel();
      if (normalizedFeedback) {
        this.ui.showInfo(`Submitted plan feedback. Regenerating...`);
      } else {
        this.ui.showInfo(`Plan approved. Proceeding to execution...`);
      }
    } catch (error) {
      this.ui.showError(`Failed to provide plan feedback: ${formatError(error)}`);
    }
  }

  async resumeTask(): Promise<void> {
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
    this.pushPanel();
    this.startPolling();
    this.ui.showInfo(`Resumed as new task ${response.taskId}`);
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

  dispose(): void {
    this.stopPolling();
    this.stopStream();
  }

  private startStream(taskId: string): void {
    if (this.streamController) return;
    this.streamController = new AbortController();
    const { signal } = this.streamController;
    const client = this.clientForSession();
    client
      .streamPatch(taskId, (event) => {
        this.patchEvents = [...this.patchEvents, event];
        this.pushPanel();
      }, signal)
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        this.ui.showWarning(`Patch stream error: ${err instanceof Error ? err.message : String(err)}`);
      })
      .finally(() => {
        this.streamController = null;
      });
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

    this.pushPanel();
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

        this.pushPanel();
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

  private clientForSession(): BackendTaskClient {
    if (!this.session) {
      throw new Error("No active session");
    }
    return this.createClient(this.session.backendBaseUrl);
  }

  private pushPanel(): void {
    this.ui.updatePanel(this.buildViewModel());
  }

  private buildViewModel(): ReviewPanelViewModel {
    const shadowWorkspacePath = this.latestResult?.shadowWorkspacePath;
    const reviewFiles =
      this.session && shadowWorkspacePath
        ? buildReviewFileEntries(
            this.session.workspacePath,
            shadowWorkspacePath,
            this.latestResult?.modifiedFiles ?? []
          )
        : [];

    return {
      session: this.session,
      task: this.latestTask,
      result: this.latestResult,
      reviewFiles,
      patchEvents: this.patchEvents,
    };
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
