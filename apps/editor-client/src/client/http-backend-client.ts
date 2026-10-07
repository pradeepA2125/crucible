import { z } from "zod";
import {
  TaskStatusSchema,
  TaskResultSchema,
  TaskSubmissionSchema,
  TaskViewSchema,
  ResumeTaskResponseSchema,
  ScopeDecisionResponseSchema,
  ValidationDecisionResponseSchema,
  CommandDecisionResponseSchema,
  ChatThreadSummarySchema,
  ChatThreadSchema,
  RewindPreviewSchema,
  RewindResultSchema,
  type RewindPreview,
  type RewindResult,
  ChatEventSchema,
  ChatMessageSchema,
  ThreadLiveStateSchema,
  SessionTranscriptSchema,
  type SessionTranscript,
  BackendConfigSchema,
  type BackendConfig,
  EffortSupportSchema,
  type EffortSupport,
  type ReasoningEffort,
  RecallTraceSchema,
  type RecallTrace,
  MemoryViewSchema,
  type MemoryView,
  SkillSummarySchema,
  AgentCatalogSchema,
  AgentDefinitionViewSchema,
  type AgentCatalog,
  type AgentDefinitionInput,
  type AgentDefinitionView,
  type SkillSummary,
  ProviderValidateResultSchema,
  type ProviderValidateResult,
  ContextTestResultSchema,
  type ContextTestResult,
  McpServerListSchema,
  type McpServerList,
  AgentDetailSchema,
  AgentSummarySchema,
  TeamDetailSchema,
  TeamActivitySchema,
  TeamPostSchema,
  TeamSummarySchema,
  ThreadUsageSchema,
  type AgentDetail,
  type AgentSummary,
  type TeamDetail,
  type TeamActivity,
  type TeamPost,
  type TeamSummary,
  type ThreadUsage,
  type BackendTaskClient,
  type ThreadLiveState,
  type PatchStreamEvent,
  type TaskResult,
  type TaskSubmission,
  type TaskView,
  type ResumeTaskRequest,
  type ResumeTaskResponse,
  type ScopeDecisionRequest,
  type ScopeDecisionResponse,
  type CommandDecision,
  type CommandDecisionResponse,
  type McpToolDecision,
  type ValidationDecisionResponse,
  type ChatThreadSummary,
  type ChatThread,
  type StreamEvent,
  type ChatMessage,
  type SequencedStreamEvent,
  type SendChatOptions,
  type SendChatResult,
  type ThreadAttention,
  ChatGPTAccountSchema,
  ChatGPTModelSchema,
  ChatGPTSignInSchema,
  type ChatGPTAccount,
  type ChatGPTModel,
  type ChatGPTSignIn,
} from "../contracts/task-contracts.js";
import type { TaskStatus } from "../domain/types.js";

interface FetchLike {
  (input: string, init?: RequestInit): Promise<Response>;
}

interface HttpBackendClientOptions {
  baseUrl: string;
  fetchFn?: FetchLike;
  /** Bearer token for this backend; called per request (a restart rewrites it). */
  authToken?: () => string | undefined;
  /** Every response reports here: ok on any non-auth status, not ok on 401/403/421. */
  onAuthStatus?: (ok: boolean, reason?: string) => void;
}

const AUTH_FAILURE_STATUSES = new Set([401, 403, 421]);

/** The backend refused this client (spec §3.5): wrong/missing token, wrong host, or
 * a check that failed before the request was sent. Not retried. */
/** The provider refused on account grounds (ChatGPT plan: signed out, plan usage off). */
export class ProviderAccessError extends Error {
  constructor(readonly kind: string, message: string) {
    super(message);
    this.name = "ProviderAccessError";
  }
}

export class BackendAuthError extends Error {
  constructor(message: string, readonly status: number | null) {
    super(message);
    this.name = "BackendAuthError";
  }
}

function withAuthHeader(init: RequestInit | undefined, token: string | undefined): RequestInit {
  const headers = { ...((init?.headers as Record<string, string> | undefined) ?? {}) };
  if (token) headers.authorization = `Bearer ${token}`;
  return { ...init, headers, redirect: "manual" };
}

// A stalled SSE connection (idle proxy/load-balancer timeout, sleep/wake, a dead TCP
// socket with no RST) never delivers `done` and never rejects — reader.read() just hangs
// forever. Observed live: a chat turn's stream went silent for 60+ minutes while the
// backend kept working; nothing detected it because nothing was watching for a gap. This
// timeout bounds how long a single reader.read() may wait before the generator gives up.
// It must comfortably exceed the longest normal silent gap between SSE events (a single
// slow local-model provider call can run 30-90s with no event in between) — 2 minutes
// gives ample margin while still recovering in a small fraction of "looks frozen forever".
const SSE_IDLE_TIMEOUT_MS = 120_000;

async function readWithIdleTimeout(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<ReadableStreamReadResult<Uint8Array>>((resolve) => {
    // Resolve with done:true (not reject) — an idle timeout should look exactly like a
    // graceful stream end to callers, not an error. The caller (chat controller) already
    // has working reconnect logic gated on the stream having ended; we just need to let
    // it actually end instead of hanging.
    timer = setTimeout(() => resolve({ done: true, value: undefined }), SSE_IDLE_TIMEOUT_MS);
  });
  try {
    return await Promise.race([reader.read(), timeout]);
  } finally {
    clearTimeout(timer);
  }
}

export class HttpBackendClient implements BackendTaskClient {
  private readonly fetchFn: FetchLike;

  constructor(private readonly options: HttpBackendClientOptions) {
    const raw = options.fetchFn ?? fetch;
    // Wrapped once here so every call — fetchJson and the raw stream/decision sites —
    // carries the token, never follows a redirect, and reports auth status.
    this.fetchFn = async (input, init) => {
      const response = await raw(input, withAuthHeader(init, options.authToken?.()));
      const status = (response as { status?: number }).status;
      if (status !== undefined && AUTH_FAILURE_STATUSES.has(status)) {
        const reason = `backend refused the request (${status})`;
        options.onAuthStatus?.(false, reason);
        throw new BackendAuthError(`${reason} for ${input}`, status);
      }
      options.onAuthStatus?.(true);
      return response;
    };
  }

  /**
   * Shared SSE line-parsing loop for the chat event stream, used by sendChatMessage,
   * postModeDecision, postClarifyDecision, and streamChannel — all four hit the same
   * `data: <ChatEvent json>` wire format and previously duplicated this ~20-line loop.
   * `doneTypes` lets callers opt into closing the stream early on a terminal event type
   * (streamChannel does this for `chat_done`/`done`; the turn-launching methods don't,
   * since their HTTP response body ending IS the natural close signal).
   */
  private async *consumeChatEventStream(
    response: Response, doneTypes: ReadonlySet<string> = new Set(),
  ): AsyncIterable<StreamEvent> {
    if (!response.body) return;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await readWithIdleTimeout(reader);
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          try {
            const event = ChatEventSchema.parse(JSON.parse(line.slice(5).trim())) as StreamEvent;
            yield event;
            if (doneTypes.has(event.type)) return;
          } catch {
            // skip malformed SSE line
          }
        }
      }
    } finally {
      reader.cancel().catch(() => {});
    }
  }

  async submitTask(input: TaskSubmission): Promise<{ taskId: string }> {
    const payload = TaskSubmissionSchema.parse(input);
    const response = await this.fetchJson("/v1/tasks", {
      method: "POST",
      body: JSON.stringify({
        goal: payload.goal,
        workspace_path: payload.workspacePath,
        mode: payload.mode
      })
    });
    return { taskId: this.readString(response, "taskId", "task_id") };
  }

  async getTask(taskId: string): Promise<TaskView> {
    const response = await this.fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}`);
    return this.toTaskView(response);
  }

  async getTaskResult(taskId: string): Promise<TaskResult> {
    const response = await this.fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/result`);
    return this.toTaskResult(response);
  }

  async cancelTask(taskId: string): Promise<{ taskId: string; status: TaskStatus }> {
    const response = await this.fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST"
    });

    const view = this.toTaskView(response);
    return {
      taskId: view.taskId,
      status: TaskStatusSchema.parse(view.status)
    };
  }

  async abortTask(taskId: string, options: { revert: boolean }): Promise<TaskView> {
    const response = await this.fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/abort`, {
      method: "POST",
      body: JSON.stringify({ revert: options.revert })
    });
    return this.toTaskView(response);
  }

  async setReviewPref(taskId: string, options: { autoAccept: boolean }): Promise<TaskView> {
    const response = await this.fetchJson(
      `/v1/tasks/${encodeURIComponent(taskId)}/review-pref`,
      {
        method: "POST",
        body: JSON.stringify({ auto_accept: options.autoAccept })
      }
    );
    return this.toTaskView(response);
  }

  async acceptPatch(taskId: string): Promise<TaskResult> {
    const response = await this.fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/accept`, {
      method: "POST"
    });
    return this.toTaskResult(response);
  }

  async rejectPatch(taskId: string, reason: string): Promise<TaskResult> {
    const response = await this.fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason })
    });
    return this.toTaskResult(response);
  }

  async providePlanFeedback(taskId: string, feedback: string | null): Promise<TaskView> {
    const response = await this.fetchJson(`/v1/tasks/${encodeURIComponent(taskId)}/plan/feedback`, {
      method: "POST",
      body: JSON.stringify({ feedback })
    });
    return this.toTaskView(response);
  }

  async sendScopeDecision(
    taskId: string,
    decision: ScopeDecisionRequest
  ): Promise<ScopeDecisionResponse> {
    const response = await this.fetchJson(
      `/v1/tasks/${encodeURIComponent(taskId)}/scope-decision`,
      {
        method: "POST",
        body: JSON.stringify({
          decision: decision.decision,
          files: decision.files ?? [],
          remember: decision.remember ?? false
        })
      }
    );
    return ScopeDecisionResponseSchema.parse({
      taskId: this.readString(response, "taskId", "task_id"),
      status: this.readString(response, "status")
    });
  }

  async sendValidationDecision(
    taskId: string,
    decision: "accept" | "reject"
  ): Promise<ValidationDecisionResponse> {
    const response = await this.fetchJson(
      `/v1/tasks/${encodeURIComponent(taskId)}/validation-decision`,
      {
        method: "POST",
        body: JSON.stringify({ decision })
      }
    );
    return ValidationDecisionResponseSchema.parse({
      taskId: this.readString(response, "taskId", "task_id"),
      status: this.readString(response, "status")
    });
  }

  async sendCommandDecision(
    taskId: string,
    decision: CommandDecision
  ): Promise<CommandDecisionResponse> {
    // camelCase → snake_case for the backend wire (ruleValue → rule_value).
    const body: Record<string, unknown> = {
      approve: decision.approve,
      remember: decision.remember,
      scope: decision.scope,
    };
    if (decision.ruleValue !== undefined) body.rule_value = decision.ruleValue;
    const response = await this.fetchJson(
      `/v1/tasks/${encodeURIComponent(taskId)}/command-decision`,
      {
        method: "POST",
        body: JSON.stringify(body)
      }
    );
    return CommandDecisionResponseSchema.parse({
      taskId: this.readString(response, "taskId", "task_id"),
      status: this.readString(response, "status")
    });
  }

  async sendStepDecision(taskId: string, decision: "accept" | "discard"): Promise<void> {
    await this.fetchJson(
      `/v1/tasks/${encodeURIComponent(taskId)}/step-decision`,
      {
        method: "POST",
        body: JSON.stringify({ decision })
      }
    );
  }

  // Controller per-edit review gate (Phase F3): a plain JSON ack — the loop's
  // continuation rides the already-open message SSE stream.
  async postEditDecision(
    threadId: string,
    decision: "accept" | "reject",
    reason?: string,
    gateId?: string
  ): Promise<void> {
    const body: Record<string, unknown> = { decision, reason: reason ?? "" };
    if (gateId !== undefined) body.gate_id = gateId;
    await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/edit-decision`,
      { method: "POST", body: JSON.stringify(body) }
    );
  }

  // One "Review each edit" value for the whole backend (spec §5.4): every thread, turn
  // and background agent. Turning auto-accept on accepts the edits waiting under it.
  async setGlobalReviewPref(options: { autoAccept: boolean }): Promise<{ autoResolved: number; background: number }> {
    const body = await this.fetchJson("/v1/chat/review-pref", {
      method: "PUT", body: JSON.stringify({ auto_accept: options.autoAccept }),
    });
    const parsed = z.object({ auto_resolved: z.number(), background: z.number() }).parse(body);
    return { autoResolved: parsed.auto_resolved, background: parsed.background };
  }

  async setPlanMode(planMode: boolean): Promise<void> {
    await this.fetchJson("/v1/chat/plan-mode", {
      method: "PUT", body: JSON.stringify({ plan_mode: planMode }),
    });
  }

  // Controller run_command gate (Phase F): a plain JSON ack — the loop's continuation
  // rides the already-open message SSE stream. Mirrors postEditDecision but carries a
  // CommandDecision (camelCase ruleValue → snake_case rule_value, like sendCommandDecision).
  async postChatCommandDecision(
    threadId: string,
    decision: CommandDecision,
    gateId?: string
  ): Promise<void> {
    const body: Record<string, unknown> = {
      approve: decision.approve,
      remember: decision.remember,
      scope: decision.scope,
    };
    if (decision.ruleValue !== undefined) body.rule_value = decision.ruleValue;
    if (gateId !== undefined) body.gate_id = gateId;
    await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/command-decision`,
      { method: "POST", body: JSON.stringify(body) }
    );
  }

  // Controller mcp_tool gate: a plain JSON ack — the loop's continuation rides the
  // already-open message SSE stream. Mirrors postChatCommandDecision.
  async postChatMcpDecision(
    threadId: string,
    decision: McpToolDecision,
    gateId?: string
  ): Promise<void> {
    const body: Record<string, unknown> = {
      approve: decision.approve, remember: decision.remember,
    };
    if (gateId !== undefined) body.gate_id = gateId;
    await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/mcp-decision`,
      { method: "POST", body: JSON.stringify(body) }
    );
  }

  // Controller mode gate (Phase F2): a STREAMED dispatch (edit/create_task produce
  // live events), consumed like sendChatMessage.
  async *postModeDecision(threadId: string, mode: string): AsyncIterable<StreamEvent> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/chat/threads/${encodeURIComponent(threadId)}/mode-decision`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode }),
      }
    );
    if (!response.ok) {
      throw new Error(`Mode decision failed (${response.status}) for thread ${threadId}`);
    }
    yield* this.consumeChatEventStream(response);
  }

  // Controller clarify gate: a STREAMED dispatch (the answer re-enters the loop),
  // consumed like sendChatMessage — mirror of postModeDecision.
  async *postClarifyDecision(threadId: string, answer: string): AsyncIterable<StreamEvent> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/chat/threads/${encodeURIComponent(threadId)}/clarify-decision`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ answer }),
      }
    );
    if (!response.ok) {
      throw new Error(`Clarify decision failed (${response.status}) for thread ${threadId}`);
    }
    yield* this.consumeChatEventStream(response);
  }

  async stopChatTurn(threadId: string): Promise<{ ok: boolean }> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/stop`,
      { method: "POST", body: "{}" }
    ) as Record<string, unknown>;
    return { ok: Boolean(raw["ok"]) };
  }

  async listAgents(threadId: string, turnId?: string): Promise<AgentSummary[]> {
    const query = turnId !== undefined ? `?turn_id=${encodeURIComponent(turnId)}` : "";
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/agents${query}`
    ) as Record<string, unknown>;
    const agents = Array.isArray(raw["agents"]) ? raw["agents"] as Record<string, unknown>[] : [];
    return agents.map((a) => AgentSummarySchema.parse(HttpBackendClient.toAgentSummary(a)));
  }

  async getAgent(threadId: string, agentId: string): Promise<AgentDetail> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/agents/${encodeURIComponent(agentId)}`
    ) as Record<string, unknown>;
    const transcript = Array.isArray(raw["transcript"])
      ? raw["transcript"] as Record<string, unknown>[] : [];
    return AgentDetailSchema.parse({
      ...HttpBackendClient.toAgentSummary({
        ...raw,
        files_changed_count: Array.isArray(raw["files_changed"]) ? raw["files_changed"].length : 0,
        report_preview: String(raw["report"] ?? "").slice(0, 200),
      }),
      prompt: raw["prompt"], report: raw["report"],
      filesChanged: raw["files_changed"] ?? [], staleRefusals: raw["stale_refusals"] ?? 0,
      transcript: transcript.map((m) => HttpBackendClient.toChatMessage(m)),
      lastSeq: raw["last_seq"] ?? 0,
    });
  }

  async stopAgent(threadId: string, agentId: string): Promise<{ ok: boolean }> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/agents/${encodeURIComponent(agentId)}/stop`,
      { method: "POST" }
    ) as Record<string, unknown>;
    return { ok: raw["ok"] === true };
  }
  async getThreadUsage(threadId: string): Promise<ThreadUsage> {
    return ThreadUsageSchema.parse(await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/usage`));
  }

  async listTeams(threadId: string): Promise<TeamSummary[]> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/teams`
    ) as Record<string, unknown>;
    const teams = Array.isArray(raw["teams"]) ? raw["teams"] as Record<string, unknown>[] : [];
    return teams.map((t) => TeamSummarySchema.parse(HttpBackendClient.toTeamSummary(t)));
  }

  async getTeam(threadId: string, teamId: string): Promise<TeamDetail> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/teams/${encodeURIComponent(teamId)}`
    ) as Record<string, unknown>;
    const posts = Array.isArray(raw["posts"]) ? raw["posts"] as Record<string, unknown>[] : [];
    return TeamDetailSchema.parse({
      ...HttpBackendClient.toTeamSummary(raw),
      posts: posts.map((p) => HttpBackendClient.toTeamPost(p)),
      lastSeq: raw["last_seq"] ?? 0,
      activity: (Array.isArray(raw["activity"]) ? raw["activity"] as Record<string, unknown>[] : [])
        .map((e) => HttpBackendClient.toTeamActivity(e)),
      lastAseq: raw["last_aseq"] ?? 0,
    });
  }

  async disbandTeam(threadId: string, teamId: string): Promise<{ phase: string | null }> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/teams/${encodeURIComponent(teamId)}/disband`,
      { method: "POST", body: "{}" }
    ) as Record<string, unknown>;
    return { phase: typeof raw["phase"] === "string" ? raw["phase"] : null };
  }

  async decideTeamPlan(
    threadId: string, gateId: string, decision: "approve" | "feedback" | "reject",
    feedback?: string,
  ): Promise<{ teamId: string; phase: string | null }> {
    const body: Record<string, unknown> = { gate_id: gateId, decision };
    if (feedback !== undefined) body.feedback = feedback;
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/team-plan-decision`,
      { method: "POST", body: JSON.stringify(body) }
    ) as Record<string, unknown>;
    return { teamId: String(raw["team_id"] ?? ""),
             phase: typeof raw["phase"] === "string" ? raw["phase"] : null };
  }


  // Subscribe-only SSE relay (no turn launch). Reuses the SSE line-parsing already
  // behind postModeDecision/streamPatch. Closes on `done`/`chat_done`.
  async *streamChannel(channelId: string, signal?: AbortSignal): AsyncIterable<SequencedStreamEvent> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/channels/${encodeURIComponent(channelId)}/stream`,
      // exactOptionalPropertyTypes: RequestInit.signal may be absent, never undefined.
      { headers: { accept: "text/event-stream" }, ...(signal ? { signal } : {}) }
    );
    if (!response.ok) {
      throw new Error(`Channel stream failed (${response.status}) for ${channelId}`);
    }
    yield* this.consumeChatEventStream(response, new Set(["chat_done", "done"]));
  }

  async resumeTask(taskId: string, options?: ResumeTaskRequest): Promise<ResumeTaskResponse> {
    const body: Record<string, unknown> = { stage: options?.stage ?? "execute" };
    if (options?.budgetOverride) {
      body["budget_override"] = {
        max_iterations: options.budgetOverride.maxIterations,
        max_tokens: options.budgetOverride.maxTokens,
        max_files_touched: options.budgetOverride.maxFilesTouched,
        max_runtime_ms: options.budgetOverride.maxRuntimeMs
      };
    }
    const response = await this.fetchJson(
      `/v1/tasks/${encodeURIComponent(taskId)}/resume`,
      { method: "POST", body: JSON.stringify(body) }
    );
    return ResumeTaskResponseSchema.parse({
      taskId: this.readString(response, "taskId", "task_id"),
      resumeOfTaskId: this.readString(response, "resumeOfTaskId", "resume_of_task_id")
    });
  }

  async streamPatch(
    taskId: string,
    onEvent: (event: PatchStreamEvent) => void,
    signal?: AbortSignal
  ): Promise<void> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/tasks/${encodeURIComponent(taskId)}/stream-patch`,
      { signal: signal ?? null, headers: { accept: "text/event-stream" } }
    );
    if (!response.ok) {
      throw new Error(`Stream failed (${response.status}) for task ${taskId}`);
    }
    if (!response.body) return;

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const event = JSON.parse(line.slice(6)) as PatchStreamEvent;
            onEvent(event);
            if (event.type === "done") return;
          } catch {
            // skip malformed SSE line
          }
        }
      }
    } finally {
      reader.cancel().catch(() => {});
    }
  }

  async *streamPatchEvents(taskId: string): AsyncIterable<PatchStreamEvent> {
    // Wraps the callback-based streamPatch as an async iterable so callers
    // can use for-await without holding an AbortController.
    const events: PatchStreamEvent[] = [];
    let notify: (() => void) | null = null;
    let streamDone = false;

    const push = (event: PatchStreamEvent) => {
      events.push(event);
      notify?.();
      notify = null;
    };

    const streamPromise = this.streamPatch(taskId, push);
    streamPromise.finally(() => {
      streamDone = true;
      notify?.();
      notify = null;
    });

    while (true) {
      if (events.length === 0 && !streamDone) {
        await new Promise<void>((resolve) => { notify = resolve; });
      }
      while (events.length > 0) {
        const event = events.shift()!;
        yield event;
        if (event.type === "done") return;
      }
      if (streamDone) return;
    }
  }

  async listChatThreads(workspacePath: string): Promise<ChatThreadSummary[]> {
    const raw = await this.fetchJson(
      `/v1/chat/threads?workspace=${encodeURIComponent(workspacePath)}`
    ) as Record<string, unknown>;
    const threads = Array.isArray(raw["threads"]) ? raw["threads"] : [];
    return (threads as Record<string, unknown>[]).map((t) =>
      ChatThreadSummarySchema.parse({
        threadId: t["thread_id"],
        workspacePath: t["workspace_path"],
        title: t["title"],
        createdAt: t["created_at"],
        updatedAt: t["updated_at"] ?? undefined,
        messageCount: t["message_count"] ?? undefined,
        status: t["status"] ?? null,
        agentsRunning: t["agents_running"] ?? 0,
      })
    );
  }

  async createChatThread(workspacePath: string, title = "New Chat"): Promise<ChatThreadSummary> {
    const raw = await this.fetchJson("/v1/chat/threads", {
      method: "POST",
      body: JSON.stringify({ workspace: workspacePath, title }),
    }) as Record<string, unknown>;
    return ChatThreadSummarySchema.parse({
      threadId: raw["thread_id"],
      workspacePath: raw["workspace_path"],
      title: raw["title"],
      createdAt: raw["created_at"],
    });
  }

  async getChatThread(threadId: string): Promise<ChatThread> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}`
    ) as Record<string, unknown>;
    const messages = Array.isArray(raw["messages"]) ? raw["messages"] : [];
    return ChatThreadSchema.parse({
      threadId: raw["thread_id"],
      workspacePath: raw["workspace_path"],
      title: raw["title"],
      messages: (messages as Record<string, unknown>[]).map((m) => HttpBackendClient.toChatMessage(m)),
      touchedFiles: Array.isArray(raw["touched_files"]) ? raw["touched_files"] : [],
    });
  }

  static toChatMessage(m: Record<string, unknown>): Record<string, unknown> {
    return {
      role: m["role"],
      content: m["content"],
      type: m["type"] ?? "text",
      // The rewind anchor. This mapping is explicit, not passthrough — omitting
      // the field here silently drops it no matter what the schema allows.
      id: m["id"] ?? null,
      taskId: m["task_id"] ?? null,
      timestamp: typeof m["timestamp"] === "string"
        ? m["timestamp"]
        : new Date(m["timestamp"] as string).toISOString(),
      metadata: (typeof m["metadata"] === "object" && m["metadata"] !== null)
        ? m["metadata"]
        : {},
    };
  }

  static toTeamActivity(e: Record<string, unknown>): Record<string, unknown> {
    return {
      teamId: e["team_id"], aseq: e["aseq"], at: e["at"], label: e["label"], kind: e["kind"],
      activation: e["activation"] ?? null, causeSeq: e["cause_seq"] ?? null,
      payload: e["payload"] ?? {},
    };
  }

  static toTeamPost(p: Record<string, unknown>): Record<string, unknown> {
    return {
      teamId: p["team_id"], seq: p["seq"], author: p["author"], kind: p["kind"],
      recipient: p["recipient"] ?? null, text: p["text"] ?? "", mentions: p["mentions"] ?? [],
      refId: p["ref_id"] ?? null, round: p["round"] ?? null, payload: p["payload"] ?? {},
      closed: p["closed"] ?? null, createdAt: p["created_at"],
    };
  }

  private static toTeamCore(t: Record<string, unknown>): Record<string, unknown> {
    return {
      teamId: t["team_id"], name: t["name"], phase: t["phase"], round: t["round"],
      maxRounds: t["max_rounds"], pausedReason: t["paused_reason"] ?? null,
    };
  }

  private static toTeamSummary(t: Record<string, unknown>): Record<string, unknown> {
    const members = Array.isArray(t["members"]) ? t["members"] as Record<string, unknown>[] : [];
    const proposals = Array.isArray(t["open_proposals"])
      ? t["open_proposals"] as Record<string, unknown>[] : [];
    return {
      ...HttpBackendClient.toTeamCore(t),
      goal: t["goal"] ?? "",
      members: members.map((m) => ({ label: m["label"], agentId: m["agent_id"],
                                     status: m["status"] ?? "", name: m["name"] ?? "",
                                     description: m["description"] ?? "",
                                     assignment: m["assignment"] ?? null,
                                     assignmentDone: m["assignment_done"] === true })),
      // Proposal keys (id/author/text) are camel-safe; stances are label → stance.
      openProposals: proposals,
      usage: t["usage"] ?? { requests: 0, budget: 0 },
      createdAt: t["created_at"] ?? "",
      endReason: t["end_reason"] ?? null,
      approvalGate: t["approval_gate"] === true,
      adoptedProposalId: t["adopted_proposal_id"] ?? null,
    };
  }

  private static toTeamLive(t: Record<string, unknown>): Record<string, unknown> {
    const members = Array.isArray(t["members"]) ? t["members"] as Record<string, unknown>[] : [];
    const latest = t["latest"] as Record<string, unknown> | null | undefined;
    const counts = t["counts"] as Record<string, unknown> | undefined;
    const rp = t["round_progress"] as Record<string, unknown> | null | undefined;
    return {
      ...HttpBackendClient.toTeamCore(t),
      members: members.map((m) => {
        const last = m["last"] as Record<string, unknown> | null | undefined;
        return {
          label: m["label"], agentId: m["agent_id"], status: m["status"] ?? "",
          last: last ? {
            kind: last["kind"], at: last["at"], causeSeq: last["cause_seq"] ?? null,
            by: last["by"] ?? null, status: last["status"] ?? null,
            activation: last["activation"] ?? null,
            waitingOn: last["waiting_on"] ?? [],
          } : null,
        };
      }),
      latest: latest ?? null,
      counts: counts ?? { posts: 0, proposals: [] },
      roundProgress: rp ? { round: rp["round"], members: rp["members"] ?? [],
                            reported: rp["reported"] ?? [] } : null,
    };
  }


  private static toAgentSummary(a: Record<string, unknown>): Record<string, unknown> {
    return {
      agentId: a["agent_id"], turnId: a["turn_id"] ?? undefined,
      parentAgentId: a["parent_agent_id"] ?? null, depth: a["depth"],
      name: a["name"], label: a["label"], status: a["status"],
      now: a["now"] ?? "", toolCount: a["tool_count"] ?? 0,
      filesChangedCount: a["files_changed_count"] ?? 0,
      startedAt: a["started_at"] ?? null, endedAt: a["ended_at"] ?? null,
      reportPreview: a["report_preview"] ?? "",
      activationCount: a["activation_count"] ?? 0, teamId: a["team_id"] ?? null,
      dispatcherId: a["dispatcher_id"] ?? null, onFinish: a["on_finish"] ?? null,
      activationStartedAt: a["activation_started_at"] ?? null,
      activationEndedAt: a["activation_ended_at"] ?? null,
    };
  }

  async getThreadLiveState(threadId: string): Promise<ThreadLiveState> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/live`
    ) as Record<string, unknown>;
    // Gates keep their payload keys (snake_case) — same passthrough as before; only the
    // envelope fields are mapped. agent keys (id/label/name) are already camel-safe.
    const toGate = (g: Record<string, unknown>) => ({
      gateId: g["gate_id"],
      kind: g["kind"],
      payload: g["payload"] ?? {},
      agent: g["agent"] ?? null,
      team: g["team"] ?? null,
    });
    const rawGates = Array.isArray(raw["pending_gates"])
      ? (raw["pending_gates"] as Record<string, unknown>[])
      : [];
    return ThreadLiveStateSchema.parse({
      activeTaskId: raw["active_task_id"] ?? null,
      status: raw["status"] ?? null,
      pendingGates: rawGates.map(toGate),
      plan: raw["plan"] ?? null,
      turnActive: raw["turn_active"] ?? false,
      failureSummary: this.toFailureSummary(raw),
      runSummary: this.toRunSummary(raw),
      taskNarrative: this.toTaskNarrative(raw),
      // The todo checklist items (title/status/note) are already in the contract shape,
      // so they pass straight through. Without this hop ThreadLiveStateSchema.todos is
      // optional and silently parses to undefined → controller.ts never posts
      // renderLiveTodos → the TodoCard never renders (the whole /live render path is dead).
      todos: raw["todos"] ?? null,
      // Session rows keep their snake keys (id/command/status/exit_code/started_at)
      // — same passthrough convention as the memory-inspect signals.
      sessions: raw["sessions"] ?? null,
      agents: Array.isArray(raw["agents"])
        ? (raw["agents"] as Record<string, unknown>[]).map((a) => HttpBackendClient.toAgentSummary(a))
        : null,
      agentsRunning: raw["agents_running"] ?? 0,
      teams: Array.isArray(raw["teams"])
        ? (raw["teams"] as Record<string, unknown>[]).map((t) => HttpBackendClient.toTeamLive(t))
        : null,
      turnKind: raw["turn_kind"] ?? null,
      messageCount: raw["message_count"] ?? 0,
      providerAccess: HttpBackendClient.toProviderAccess(raw["provider_access"]),
    });
  }

  private static toProviderAccess(raw: unknown): unknown {
    if (raw === null || typeof raw !== "object") return null;
    const a = raw as Record<string, unknown>;
    return {
      kind: a["kind"], message: a["message"], code: a["code"] ?? null,
      status: a["status"] ?? null, requestId: a["request_id"] ?? null,
    };
  }

  async getSessionTranscript(threadId: string, sessionId: string): Promise<SessionTranscript> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/sessions/${encodeURIComponent(sessionId)}/transcript`
    );
    return SessionTranscriptSchema.parse(raw);
  }

  async getConfig(): Promise<BackendConfig> {
    const raw = await this.fetchJson("/v1/config") as Record<string, unknown>;
    return BackendConfigSchema.parse({
      taskSubsystemEnabled: raw["task_subsystem_enabled"] ?? false,
      chatControllerEnabled: raw["chat_controller_enabled"] ?? false,
      memoryEnabled: raw["memory_enabled"] ?? false,
      skillsEnabled: raw["skills_enabled"] ?? false,
      mcpEnabled: raw["mcp_enabled"] ?? false,
      subagentsEnabled: raw["subagents_enabled"] ?? false,
      teamsEnabled: raw["teams_enabled"] ?? false,
      provider: HttpBackendClient.mapProvider(raw["provider"]),
    });
  }

  private static mapProvider(raw: unknown): unknown {
    if (raw === null || typeof raw !== "object") return null;
    const p = raw as Record<string, unknown>;
    return {
      backend: p["backend"],
      model: p["model"],
      usesChatgptPlan: p["uses_chatgpt_plan"] ?? false,
      // Absent on an older backend; null when the process has no window configured.
      ...(p["context_window"] !== undefined ? { contextWindow: p["context_window"] } : {}),
      ...(p["reasoning_effort"] !== undefined ? { reasoningEffort: p["reasoning_effort"] } : {}),
      ...(p["reasoning_effort_note"] !== undefined
        ? { reasoningEffortNote: p["reasoning_effort_note"] }
        : {}),
      ...(p["reasoning_effort_support"] !== undefined
        ? { reasoningEffortSupport: p["reasoning_effort_support"] }
        : {}),
    };
  }

  async previewRewind(threadId: string, messageId: string): Promise<RewindPreview> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/rewind-preview`
      + `?message_id=${encodeURIComponent(messageId)}`
    ) as Record<string, unknown>;
    return RewindPreviewSchema.parse({
      messages: raw["messages"],
      files: raw["files"],
      commandsRun: raw["commands_run"],
      blockedByTask: raw["blocked_by_task"] ?? null,
      blockedByAgents: raw["blocked_by_agents"] ?? [],
      sessions: raw["sessions"] ?? [],
    });
  }

  async rewindThread(threadId: string, messageId: string): Promise<RewindResult> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/rewind`,
      { method: "POST", body: JSON.stringify({ message_id: messageId }) }
    ) as Record<string, unknown>;
    return RewindResultSchema.parse({
      restoredFiles: raw["restored_files"] ?? [],
      deletedFiles: raw["deleted_files"] ?? [],
      oversizeFiles: raw["oversize_files"] ?? [],
      failed: raw["failed"] ?? [],
      removedMessages: raw["removed_messages"],
      prefillText: raw["prefill_text"] ?? "",
      retiredMemories: raw["retired_memories"] ?? 0,
    });
  }

  /** Like fetchJson, but a failure carries the backend's `detail` as its message: the
   * Settings › Agents UI shows it verbatim ("the file changed since you reviewed it"). */
  private async fetchJsonDetail(path: string, init: RequestInit = {}): Promise<unknown> {
    const response = await this.fetchFn(`${this.options.baseUrl}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...((init.headers as Record<string, string>) ?? {}) },
    });
    if (!response.ok) {
      let detail = `Backend request failed (${response.status} ${response.statusText}) for ${path}`;
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
      } catch { /* not JSON — keep the generic message */ }
      const error = new Error(detail) as Error & { status?: number };
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  private static toAgentView(raw: Record<string, unknown>): AgentDefinitionView {
    return AgentDefinitionViewSchema.parse({
      name: raw["name"], description: raw["description"],
      tools: raw["tools"] ?? null,
      disallowedTools: raw["disallowed_tools"] ?? [],
      permission: raw["permission"], declaredPermission: raw["declared_permission"],
      model: raw["model"], maxTurns: raw["max_turns"] ?? null, skills: raw["skills"] ?? [],
      source: raw["source"], path: raw["path"] ?? null, sha256: raw["sha256"] ?? null,
      trust: raw["trust"], active: raw["active"], warnings: raw["warnings"] ?? [],
      shadowedBy: raw["shadowed_by"] ?? null, persona: raw["persona"] ?? "",
      content: raw["content"] ?? null,
    });
  }

  async listAgentDefinitions(): Promise<AgentCatalog> {
    const raw = await this.fetchJsonDetail("/v1/agents") as Record<string, unknown>;
    const agents = Array.isArray(raw["agents"]) ? raw["agents"] as Record<string, unknown>[] : [];
    return AgentCatalogSchema.parse({
      agents: agents.map((a) => HttpBackendClient.toAgentView(a)),
      skipped: raw["skipped"] ?? [],
      availableTools: raw["available_tools"] ?? [],
    });
  }

  async saveAgentDefinition(name: string, input: AgentDefinitionInput): Promise<AgentDefinitionView> {
    const raw = await this.fetchJsonDetail(`/v1/agents/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({
        description: input.description, persona: input.persona, tools: input.tools,
        disallowed_tools: input.disallowedTools, permission: input.permission,
        model: input.model, max_turns: input.maxTurns, skills: input.skills,
        ...(input.renameFrom !== undefined ? { rename_from: input.renameFrom } : {}),
      }),
    }) as { agent: Record<string, unknown> };
    return HttpBackendClient.toAgentView(raw.agent);
  }

  async deleteAgentDefinition(name: string): Promise<void> {
    await this.fetchJsonDetail(`/v1/agents/${encodeURIComponent(name)}`, { method: "DELETE" });
  }

  async trustAgentDefinition(path: string, sha256: string): Promise<void> {
    await this.fetchJsonDetail("/v1/agents/trust", {
      method: "POST", body: JSON.stringify({ path, sha256 }) });
  }

  async startChatGPTSignIn(
    req: { registrationId?: string; reconsent?: boolean } = {},
  ): Promise<ChatGPTSignIn & { authorizeUrl: string }> {
    const raw = await this.fetchJsonDetail("/v1/auth/chatgpt/attempts", {
      method: "POST",
      body: JSON.stringify({
        registration_id: req.registrationId ?? null, reconsent: req.reconsent ?? false }),
    }) as Record<string, unknown>;
    return { ...HttpBackendClient.toSignIn(raw), authorizeUrl: String(raw["authorize_url"]) };
  }

  async getChatGPTSignIn(attemptId: string): Promise<ChatGPTSignIn> {
    return HttpBackendClient.toSignIn(await this.fetchJsonDetail(
      `/v1/auth/chatgpt/attempts/${encodeURIComponent(attemptId)}`) as Record<string, unknown>);
  }

  async cancelChatGPTSignIn(attemptId: string): Promise<ChatGPTSignIn> {
    return HttpBackendClient.toSignIn(await this.fetchJsonDetail(
      `/v1/auth/chatgpt/attempts/${encodeURIComponent(attemptId)}/cancel`,
      { method: "POST" }) as Record<string, unknown>);
  }

  async listChatGPTAccounts(): Promise<ChatGPTAccount[]> {
    const raw = await this.fetchJsonDetail("/v1/auth/chatgpt/registrations") as {
      registrations?: Record<string, unknown>[] };
    return (raw.registrations ?? []).map((r) => ChatGPTAccountSchema.parse({
      registrationId: r["registration_id"], label: r["label"], email: r["email"] ?? null,
      name: r["name"] ?? null, planEnabled: r["plan_enabled"], signedIn: r["signed_in"],
    }));
  }

  async signOutChatGPT(registrationId: string): Promise<{ remoteRevoked: boolean }> {
    const raw = await this.fetchJsonDetail(
      `/v1/auth/chatgpt/registrations/${encodeURIComponent(registrationId)}/sign-out`,
      { method: "POST" }) as Record<string, unknown>;
    return { remoteRevoked: raw["remote_revoked"] === true };
  }

  /** The account's model catalog. A refusal on account grounds (signed out, plan usage
   * off) throws ProviderAccessError so the UI can show the right action. */
  async listChatGPTModels(registrationId: string): Promise<ChatGPTModel[]> {
    const path = `/v1/auth/chatgpt/registrations/${encodeURIComponent(registrationId)}/models`;
    const response = await this.fetchFn(`${this.options.baseUrl}${path}`, {
      method: "POST", headers: { "content-type": "application/json" } });
    const body = await response.json().catch(() => ({})) as Record<string, unknown>;
    if (!response.ok) {
      const detail = body["detail"];
      if (detail !== null && typeof detail === "object") {
        const d = detail as Record<string, unknown>;
        throw new ProviderAccessError(String(d["kind"]), String(d["message"]));
      }
      throw new Error(typeof detail === "string" ? detail
        : `Backend request failed (${response.status}) for ${path}`);
    }
    const models = Array.isArray(body["models"]) ? body["models"] as Record<string, unknown>[] : [];
    return models.map((m) => ChatGPTModelSchema.parse({
      slug: m["slug"], displayName: m["display_name"], contextWindow: m["context_window"] ?? null }));
  }

  private static toSignIn(raw: Record<string, unknown>): ChatGPTSignIn {
    return ChatGPTSignInSchema.parse({
      attemptId: raw["attempt_id"], state: raw["state"],
      registrationId: raw["registration_id"] ?? null, planEnabled: raw["plan_enabled"] ?? null,
      firstPlanSignIn: raw["first_plan_sign_in"] ?? false, reason: raw["reason"] ?? null,
      message: raw["message"] ?? null,
    });
  }

  async listSkills(workspace: string): Promise<SkillSummary[]> {
    const raw = await this.fetchJson(
      `/v1/skills?workspace=${encodeURIComponent(workspace)}`
    ) as { skills?: unknown[] };
    return (raw.skills ?? []).map((s) => SkillSummarySchema.parse(s));
  }

  async validateProvider(req: {
    backend: string;
    model?: string;
    credentials?: Record<string, string>;
  }): Promise<ProviderValidateResult> {
    const raw = await this.fetchJson("/v1/providers/validate", {
      method: "POST",
      body: JSON.stringify({
        backend: req.backend,
        model: req.model ?? null,
        credentials: req.credentials ?? {},
      }),
    }) as Record<string, unknown>;
    // json_mode -> jsonMode; every other key is already camelCase-compatible.
    // The route omits json_mode/warning entirely when it has nothing to say
    // (see routes.py), so both are simply absent from `raw` in that case.
    return ProviderValidateResultSchema.parse({
      ...raw,
      ...(raw["json_mode"] !== undefined ? { jsonMode: raw["json_mode"] } : {}),
    });
  }

  async setProvider(req: {
    backend: string;
    model?: string;
    credentials?: Record<string, string>;
    contextWindow?: number;
    reasoningEffort?: ReasoningEffort;
  }): Promise<{
    backend: string;
    model: string;
    reasoningEffort?: ReasoningEffort | null;
    reasoningEffortNote?: string | null;
    reasoningEffortSupport?: EffortSupport;
  }> {
    const raw = await this.fetchJson("/v1/config/provider", {
      method: "PUT",
      body: JSON.stringify({
        backend: req.backend,
        model: req.model ?? null,
        credentials: req.credentials ?? {},
        // Omitted, not nulled, when the caller has nothing to say: the route reads
        // absent as "leave the window alone", which is what a model-only hot-swap
        // from the composer needs.
        ...(req.contextWindow !== undefined ? { context_window: req.contextWindow } : {}),
        ...(req.reasoningEffort !== undefined ? { reasoning_effort: req.reasoningEffort } : {}),
      }),
    }) as Record<string, unknown>;
    return {
      backend: String(raw["backend"]),
      model: String(raw["model"]),
      ...(raw["reasoning_effort"] !== undefined
        ? { reasoningEffort: raw["reasoning_effort"] as ReasoningEffort | null }
        : {}),
      ...(raw["reasoning_effort_note"] !== undefined
        ? { reasoningEffortNote: raw["reasoning_effort_note"] as string | null }
        : {}),
      ...(raw["reasoning_effort_support"] !== undefined
        ? { reasoningEffortSupport: EffortSupportSchema.parse(raw["reasoning_effort_support"]) }
        : {}),
    };
  }

  async testContextWindow(req: {
    backend: string;
    model?: string;
    credentials?: Record<string, string>;
    contextWindow: number;
  }): Promise<ContextTestResult> {
    const raw = await this.fetchJson("/v1/providers/context-test", {
      method: "POST",
      body: JSON.stringify({
        backend: req.backend,
        model: req.model ?? null,
        credentials: req.credentials ?? {},
        context_window: req.contextWindow,
      }),
    }) as Record<string, unknown>;
    return ContextTestResultSchema.parse({
      ...raw,
      ...(raw["prompt_tokens"] !== undefined ? { promptTokens: raw["prompt_tokens"] } : {}),
    });
  }

  async listMcpServers(): Promise<McpServerList> {
    const raw = await this.fetchJson("/v1/mcp/servers");
    return HttpBackendClient.mapMcpList(raw);
  }

  async upsertMcpServer(
    name: string,
    entry: Record<string, unknown>,
    disabled: string[]
  ): Promise<McpServerList> {
    const raw = await this.fetchJson(`/v1/mcp/servers/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ entry, disabled }),
    });
    return HttpBackendClient.mapMcpList(raw);
  }

  async deleteMcpServer(name: string, disabled: string[]): Promise<McpServerList> {
    const raw = await this.fetchJson(`/v1/mcp/servers/${encodeURIComponent(name)}`, {
      method: "DELETE",
      body: JSON.stringify({ disabled }),
    });
    return HttpBackendClient.mapMcpList(raw);
  }

  async reconnectMcpServer(name: string, disabled: string[]): Promise<McpServerList> {
    const raw = await this.fetchJson(
      `/v1/mcp/servers/${encodeURIComponent(name)}/reconnect`,
      { method: "POST", body: JSON.stringify({ disabled }) }
    );
    return HttpBackendClient.mapMcpList(raw);
  }

  private static mapMcpList(raw: unknown): McpServerList {
    const wire = raw as {
      enabled?: boolean;
      servers?: Record<string, unknown>[];
    };
    return McpServerListSchema.parse({
      enabled: wire.enabled ?? false,
      servers: (wire.servers ?? []).map((s) => ({
        name: s["name"],
        transport: s["transport"],
        enabledInFile: s["enabled_in_file"] ?? false,
        state: s["state"],
        detail: s["detail"] ?? null,
        toolCount: s["tool_count"] ?? 0,
      })),
    });
  }

  async getMemoryInspect(threadId: string): Promise<RecallTrace | null> {
    const raw = await this.fetchJson(
      `/v1/memory/inspect?thread_id=${encodeURIComponent(threadId)}`
    ) as Record<string, unknown>;
    // Soft-empty: the route returns {} or {entries: []} when no trace is recorded yet.
    const entries = raw["entries"] as unknown[] | undefined;
    if (raw["query"] === undefined || !entries || entries.length === 0) {
      return null;
    }
    return RecallTraceSchema.parse(mapRecallTrace(raw));
  }

  async listMemories(filter: {
    scopeKind: string;
    scopeId: string;
    kind?: string;
    includeRetired?: boolean;
  }): Promise<MemoryView[]> {
    const params = new URLSearchParams({ scope_kind: filter.scopeKind, scope_id: filter.scopeId });
    if (filter.kind) params.set("kind", filter.kind);
    if (filter.includeRetired) params.set("include_retired", "true");
    const raw = await this.fetchJson(`/v1/memory?${params.toString()}`) as Record<string, unknown>[];
    return raw.map((m) => MemoryViewSchema.parse(mapMemoryView(m)));
  }

  async getSupersedeChain(memoryId: string): Promise<MemoryView[]> {
    const raw = await this.fetchJson(
      `/v1/memory/${encodeURIComponent(memoryId)}/chain`
    ) as Record<string, unknown>[];
    return raw.map((m) => MemoryViewSchema.parse(mapMemoryView(m)));
  }

  async sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: SendChatOptions): Promise<SendChatResult> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/chat/threads/${encodeURIComponent(threadId)}/message`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          content: message,
          ...(options?.stepReview !== undefined ? { step_review: options.stepReview } : {}),
          ...(options?.forcedSkills && options.forcedSkills.length
            ? { forced_skills: options.forcedSkills }
            : {}),
          ...(options?.mentionedFiles && options.mentionedFiles.length
            ? { mentioned_files: options.mentionedFiles }
            : {}),
          ...(options?.planMode !== undefined ? { plan_mode: options.planMode } : {}),
          // Lets the caller's optimistic echo share the persisted message's id, so a
          // rewind anchor exists on the message you just sent without a reload.
          ...(options?.messageId ? { message_id: options.messageId } : {}),
        }),
        signal: signal ?? null,
      }
    );
    if (response.status === 202) {
      // Spec §5.3: a notice turn is running; the backend queued the message.
      const body = z.object({ queued: z.literal(true), message_id: z.string() })
        .parse(await response.json());
      return { kind: "queued", messageId: body.message_id };
    }
    if (!response.ok) {
      const error = new Error(
        `Chat message failed (${response.status}) for thread ${threadId}`
      ) as Error & { status?: number };
      error.status = response.status;
      throw error;
    }
    return { kind: "stream", events: this.consumeChatEventStream(response) };
  }

  async getAttention(workspacePath: string): Promise<ThreadAttention[]> {
    const raw = await this.fetchJson(
      `/v1/chat/attention?workspace=${encodeURIComponent(workspacePath)}`
    );
    const rows = z.array(z.object({
      thread_id: z.string(), pending_gates: z.number(), agents_running: z.number(),
    })).parse(raw);
    return rows.map((r) => ({
      threadId: r.thread_id, pendingGates: r.pending_gates, agentsRunning: r.agents_running,
    }));
  }

  async stopAllAgents(threadId: string): Promise<number> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/agents/stop-all`, { method: "POST" });
    return z.object({ stopped: z.number() }).parse(raw).stopped;
  }

  async applyInlineChange(inlineTaskId: string): Promise<void> {
    await this.fetchJson(
      `/v1/chat/inline-changes/${encodeURIComponent(inlineTaskId)}/promote`,
      { method: "POST" }
    );
  }

  async discardInlineChange(inlineTaskId: string): Promise<void> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/chat/inline-changes/${encodeURIComponent(inlineTaskId)}`,
      { method: "DELETE", headers: { "content-type": "application/json" } }
    );
    if (!response.ok) {
      throw new Error(
        `Backend request failed (${response.status} ${response.statusText}) for discardInlineChange`);
    }
  }

  private async fetchJson(path: string, init: RequestInit = {}): Promise<unknown> {
    const response = await this.fetchFn(`${this.options.baseUrl}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        ...(init.headers ?? {})
      }
    });

    if (!response.ok) {
      const error = new Error(
        `Backend request failed (${response.status} ${response.statusText}) for ${path}`
      ) as Error & { status?: number };
      // Surface the status so callers can treat a benign 409 (gate already moved on)
      // differently from a real failure, without parsing the message string.
      error.status = response.status;
      throw error;
    }

    return response.json();
  }

  // Tier B durable telemetry: map the snake_case wire summaries to camelCase, or
  // undefined when absent/null so the optional schema fields pass.
  private toFailureSummary(raw: unknown): unknown | undefined {
    const value = this.readOptionalUnknown(raw, "failureSummary", "failure_summary");
    if (!value || typeof value !== "object") return undefined;
    const r = value as Record<string, unknown>;
    return {
      stepId: r["stepId"] ?? r["step_id"] ?? null,
      stepIndex: r["stepIndex"] ?? r["step_index"] ?? null,
      errorClass: r["errorClass"] ?? r["error_class"],
      message: r["message"]
    };
  }

  private toRunSummary(raw: unknown): unknown | undefined {
    const value = this.readOptionalUnknown(raw, "runSummary", "run_summary");
    if (!value || typeof value !== "object") return undefined;
    const r = value as Record<string, unknown>;
    return {
      stepsCompleted: r["stepsCompleted"] ?? r["steps_completed"],
      stepsTotal: r["stepsTotal"] ?? r["steps_total"],
      deviations: r["deviations"] ?? []
    };
  }

  private toTaskNarrative(raw: unknown): unknown | undefined {
    const value = this.readOptionalUnknown(raw, "taskNarrative", "task_narrative");
    if (!value || typeof value !== "object") return undefined;
    const r = value as Record<string, unknown>;
    return {
      outcome: r["outcome"],
      headline: r["headline"],
      points: r["points"] ?? []
    };
  }

  private toTaskView(raw: unknown): TaskView {
    return TaskViewSchema.parse({
      taskId: this.readString(raw, "taskId", "task_id"),
      status: this.readUnknown(raw, "status"),
      goal: this.readString(raw, "goal"),
      modifiedFiles: this.readArray(raw, "modifiedFiles", "modified_files"),
      diagnostics: this.normalizeDiagnostics(raw),
      planMarkdown: this.readOptionalString(raw, "planMarkdown", "plan_markdown"),
      resumeOfTaskId: this.readOptionalString(raw, "resumeOfTaskId", "resume_of_task_id"),
      failureSummary: this.toFailureSummary(raw),
      runSummary: this.toRunSummary(raw),
      taskNarrative: this.toTaskNarrative(raw)
    });
  }

  private toTaskResult(raw: unknown): TaskResult {
    return TaskResultSchema.parse({
      taskId: this.readString(raw, "taskId", "task_id"),
      status: this.readUnknown(raw, "status"),
      plan: this.readOptionalUnknown(raw, "plan"),
      planMarkdown: this.readOptionalString(raw, "planMarkdown", "plan_markdown"),
      patch: this.readOptionalUnknown(raw, "patch"),
      modifiedFiles: this.readArray(raw, "modifiedFiles", "modified_files"),
      diagnostics: this.normalizeDiagnostics(raw),
      promotedAt: this.readOptionalNullableString(raw, "promotedAt", "promoted_at"),
      shadowWorkspacePath: this.readOptionalNullableString(
        raw,
        "shadowWorkspacePath",
        "shadow_workspace_path"
      ),
      resumeOfTaskId: this.readOptionalString(raw, "resumeOfTaskId", "resume_of_task_id"),
      failureSummary: this.toFailureSummary(raw),
      runSummary: this.toRunSummary(raw),
      taskNarrative: this.toTaskNarrative(raw)
    });
  }

  private normalizeDiagnostics(raw: unknown): unknown[] {
    const diagnostics = this.readArray(raw, "diagnostics");
    return diagnostics.map((item) => this.normalizeDiagnostic(item));
  }

  private normalizeDiagnostic(raw: unknown): Record<string, unknown> {
    const record = this.readRecord(raw);
    const normalized: Record<string, unknown> = {
      source: this.readString(record, "source"),
      message: this.readString(record, "message"),
      level: this.readString(record, "level")
    };

    this.assignNullableString(record, normalized, "file");
    this.assignNullableInteger(record, normalized, "line");
    this.assignNullableInteger(record, normalized, "column");
    return normalized;
  }

  private assignNullableString(
    source: Record<string, unknown>,
    target: Record<string, unknown>,
    key: string
  ): void {
    const value = source[key];
    if (value === undefined || value === null) {
      return;
    }
    target[key] = String(value);
  }

  private assignNullableInteger(
    source: Record<string, unknown>,
    target: Record<string, unknown>,
    key: string
  ): void {
    const value = source[key];
    if (value === undefined || value === null) {
      return;
    }
    target[key] = value;
  }

  private readRecord(raw: unknown): Record<string, unknown> {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error("Unexpected backend payload shape");
    }
    return raw as Record<string, unknown>;
  }

  private readUnknown(raw: unknown, key: string, fallbackKey?: string): unknown {
    const record = this.readRecord(raw);
    if (key in record) {
      return record[key];
    }
    if (fallbackKey && fallbackKey in record) {
      return record[fallbackKey];
    }
    throw new Error(`Missing required field '${key}' from backend payload`);
  }

  private readOptionalUnknown(raw: unknown, key: string, fallbackKey?: string): unknown | undefined {
    const record = this.readRecord(raw);
    if (key in record) {
      return record[key];
    }
    if (fallbackKey && fallbackKey in record) {
      return record[fallbackKey];
    }
    return undefined;
  }

  private readString(raw: unknown, key: string, fallbackKey?: string): string {
    const value = this.readUnknown(raw, key, fallbackKey);
    return String(value);
  }

  private readOptionalString(raw: unknown, key: string, fallbackKey?: string): string | undefined {
    const value = this.readOptionalUnknown(raw, key, fallbackKey);
    if (value === undefined || value === null) {
      return undefined;
    }
    return String(value);
  }

  private readArray(raw: unknown, key: string, fallbackKey?: string): unknown[] {
    const value = this.readUnknown(raw, key, fallbackKey);
    if (!Array.isArray(value)) {
      throw new Error(`Field '${key}' must be an array`);
    }
    return value;
  }

  private readOptionalNullableString(
    raw: unknown,
    key: string,
    fallbackKey?: string
  ): string | null | undefined {
    const value = this.readOptionalUnknown(raw, key, fallbackKey);
    if (value === undefined || value === null) {
      return value as null | undefined;
    }
    return String(value);
  }
}

function mapMemoryView(m: Record<string, unknown>): Record<string, unknown> {
  return {
    id: m["id"],
    scopeKind: m["scope_kind"],
    scopeId: m["scope_id"],
    kind: m["kind"],
    content: m["content"],
    entities: m["entities"] ?? [],
    importance: m["importance"],
    validFrom: m["valid_from"],
    validTo: m["valid_to"] ?? null,
    supersededBy: m["superseded_by"] ?? null,
    sourceKind: m["source_kind"],
    sourceRef: m["source_ref"],
    sourceSeqLo: m["source_seq_lo"] ?? null,
    sourceSeqHi: m["source_seq_hi"] ?? null,
    createdAt: m["created_at"],
  };
}

function mapRecallTrace(t: Record<string, unknown>): Record<string, unknown> {
  const entries = (t["entries"] as Record<string, unknown>[]).map((e) => ({
    memoryId: e["memory_id"],
    kind: e["kind"],
    content: e["content"],
    importance: e["importance"],
    signals: e["signals"], // keys are single words — identical in snake/camel
    fusedScore: e["fused_score"],
    rerankScore: e["rerank_score"] ?? null,
    finalRank: e["final_rank"],
    injected: e["injected"],
  }));
  return {
    query: t["query"],
    scopeKind: t["scope_kind"],
    scopeId: t["scope_id"],
    k: t["k"],
    floor: t["floor"],
    reranked: t["reranked"],
    entries,
  };
}

/** A chat message as the backend serializes it (snake_case) → the contract shape. */
export function parseWireChatMessage(raw: Record<string, unknown>): ChatMessage {
  return ChatMessageSchema.parse(HttpBackendClient.toChatMessage(raw));
}

export function parseWireTeamPost(raw: Record<string, unknown>): TeamPost {
  return TeamPostSchema.parse(HttpBackendClient.toTeamPost(raw));
}

export function parseWireTeamActivity(raw: Record<string, unknown>): TeamActivity {
  return TeamActivitySchema.parse(HttpBackendClient.toTeamActivity(raw));
}
