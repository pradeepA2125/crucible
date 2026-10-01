import { z } from "zod";
import { DiagnosticsSchema, PlanSchema } from "../domain/schemas.js";
import type { PatchOperation, PlanDocument, TaskRecord, TaskStatus } from "../domain/types.js";

export type { PatchOperation, PlanDocument, TaskRecord, TaskStatus };

export const TaskStatusSchema = z.enum([
  "QUEUED",
  "CONTEXT_READY",
  "AWAITING_PLAN_APPROVAL",
  "PLANNED",
  "EXECUTING",
  "AWAITING_SCOPE_DECISION",
  "AWAITING_STEP_REVIEW",
  "VALIDATING",
  "REPAIRING",
  "AWAITING_VALIDATION_DECISION",
  "AWAITING_COMMAND_DECISION",
  "VALIDATED",
  "READY_FOR_REVIEW",
  "PROMOTING",
  "SUCCEEDED",
  "FAILED",
  "ABORTED"
]);

export const TaskSubmissionSchema = z.object({
  goal: z.string().min(1),
  workspacePath: z.string().min(1),
  mode: z.enum(["inline", "file_edit", "project_edit", "autonomous"])
});

// Durable lifecycle telemetry (Tier B). camelCase mirror of backend FailureSummary/
// RunSummary; lets the Error/Review cards render from state on reload.
export const FailureSummarySchema = z.object({
  stepId: z.string().nullable().optional(),
  stepIndex: z.number().int().nullable().optional(),
  errorClass: z.string(),
  message: z.string()
});
export type FailureSummary = z.infer<typeof FailureSummarySchema>;

export const RunSummarySchema = z.object({
  stepsCompleted: z.number().int(),
  stepsTotal: z.number().int(),
  deviations: z.array(z.string()).default([])
});
export type RunSummary = z.infer<typeof RunSummarySchema>;

// LLM-authored narrative of the run (headline + points), for the Review/Error cards.
export const TaskNarrativeSchema = z.object({
  outcome: z.enum(["succeeded", "failed", "aborted"]),
  headline: z.string(),
  points: z.array(z.string()).default([])
});
export type TaskNarrative = z.infer<typeof TaskNarrativeSchema>;

export const TaskViewSchema = z.object({
  taskId: z.string().min(1),
  status: TaskStatusSchema,
  goal: z.string().min(1),
  modifiedFiles: z.array(z.string()),
  diagnostics: DiagnosticsSchema,
  planMarkdown: z.string().optional(),
  resumeOfTaskId: z.string().optional(),
  failureSummary: FailureSummarySchema.nullable().optional(),
  runSummary: RunSummarySchema.nullable().optional(),
  taskNarrative: TaskNarrativeSchema.nullable().optional()
});

export const TaskResultSchema = z.object({
  taskId: z.string().min(1),
  status: TaskStatusSchema,
  plan: PlanSchema.optional(),
  planMarkdown: z.string().optional(),
  patch: z.unknown().optional(),
  modifiedFiles: z.array(z.string()),
  diagnostics: DiagnosticsSchema,
  promotedAt: z.string().nullable().optional(),
  shadowWorkspacePath: z.string().nullable().optional(),
  resumeOfTaskId: z.string().optional(),
  failureSummary: FailureSummarySchema.nullable().optional(),
  runSummary: RunSummarySchema.nullable().optional(),
  taskNarrative: TaskNarrativeSchema.nullable().optional()
});

export const ResumeTaskRequestSchema = z.object({
  stage: z.enum(["plan", "feedback", "execute", "validate"]),
  budgetOverride: z.object({
    maxIterations: z.number().int().optional(),
    maxTokens: z.number().int().optional(),
    maxFilesTouched: z.number().int().optional(),
    maxRuntimeMs: z.number().int().optional()
  }).optional()
});

export const ResumeTaskResponseSchema = z.object({
  taskId: z.string().min(1),
  resumeOfTaskId: z.string().min(1)
});

export const ScopeDecisionRequestSchema = z.object({
  decision: z.enum(["approve", "reject"]),
  files: z.array(z.string()).default([]),
  remember: z.boolean().default(false)
});

export const StepDecisionRequestSchema = z.object({
  decision: z.enum(["accept", "discard"])
});

export const ScopeDecisionResponseSchema = z.object({
  taskId: z.string().min(1),
  status: TaskStatusSchema
});

export const ValidationDecisionRequestSchema = z.object({
  decision: z.enum(["accept", "reject"])
});

export const ValidationDecisionResponseSchema = z.object({
  taskId: z.string().min(1),
  status: TaskStatusSchema
});

export const CommandDecisionSchema = z.object({
  approve: z.boolean(),
  remember: z.boolean().default(false),
  scope: z.enum(["exact", "prefix", "binary"]).default("exact"),
  // For approve+remember: the rule value the UI chose (shlex-joined leading
  // tokens for "prefix", full shlex-join for "exact"; omitted for "binary",
  // engine derives basename). Mirrors backend CommandDecision.rule_value.
  ruleValue: z.string().optional(),
});

export const CommandDecisionResponseSchema = z.object({
  taskId: z.string().min(1),
  status: TaskStatusSchema
});

export type TaskSubmission = z.infer<typeof TaskSubmissionSchema>;
export type TaskView = z.infer<typeof TaskViewSchema>;
export type TaskResult = z.infer<typeof TaskResultSchema>;
export type ResumeTaskRequest = z.infer<typeof ResumeTaskRequestSchema>;
export type ResumeTaskResponse = z.infer<typeof ResumeTaskResponseSchema>;
export type ScopeDecisionRequest = z.infer<typeof ScopeDecisionRequestSchema>;
export type ScopeDecisionResponse = z.infer<typeof ScopeDecisionResponseSchema>;
export type StepDecisionRequest = z.infer<typeof StepDecisionRequestSchema>;
export type ValidationDecisionRequest = z.infer<typeof ValidationDecisionRequestSchema>;
export type ValidationDecisionResponse = z.infer<typeof ValidationDecisionResponseSchema>;
export type CommandDecision = z.infer<typeof CommandDecisionSchema>;
export type CommandDecisionResponse = z.infer<typeof CommandDecisionResponseSchema>;

// User decision on an mcp_tool approval gate (chat controller). Remember persists
// the exact (server, tool) pair to the workspace's approved-mcp-tools.json.
export interface McpToolDecision {
  approve: boolean;
  remember: boolean;
}

export interface DiffEntry {
  path: string;
  additions: number;
  deletions: number;
  tempPath: string;
}

export type StreamEvent =
  | { type: "operation_success"; payload: { op_type: string; path: string } }
  | { type: "operation_error"; payload: { op_type: string; path: string; error: string } }
  // done carries {status} when the engine reaches a terminal/pause state (engine.py:1550) and {} on bare pause paths
  | { type: "done"; payload: { status?: string } }
  | { type: "tool_call"; payload: { tool: string; thought: string; iteration?: number; phase?: string; args?: Record<string, unknown>; call_index?: number } }
  | { type: "tool_result"; payload: { tool?: string; output: string; is_error: boolean; iteration?: number; call_index?: number } }
  | { type: "planning_tool_call"; payload: { tool: string; thought: string; iteration: number; args?: Record<string, unknown> } }
  | { type: "planning_tool_result"; payload: { tool: string; output: string; is_error: boolean; iteration: number } }
  | { type: "explore_tool_result"; payload: { tool: string; output: string; is_error: boolean } }
  | { type: "planning_thinking_chunk"; payload: { chunk: string; iteration: number } }
  | { type: "planning_complete"; payload: { files_examined: string[]; confidence: string } }
  | { type: "revision_needed"; payload: { step_id: string; reason: string; evidence: string } }
  | { type: "patch_applied"; payload: { step_id: string; phase: string; touched_files: string[] } }
  | { type: "patch_failed"; payload: { step_id: string; error: string } }
  | { type: "step_started"; payload: { step_id: string; step_title: string; step_index: number; total_steps: number } }
  | { type: "scope_extension_requested"; payload: { decision_id: string; files: string[]; reason: string; step_id: string } }
  | { type: "validation_decision_requested"; payload: { task_id: string; diagnostics: Array<{ source: string; message: string; level: string }> } }
  | { type: "command_approval_requested"; payload: { decision_id: string; command: string; args: string[]; cwd: string; step_id: string } }
  | { type: "mcp_approval_requested"; payload: { server: string; tool: string; args: Record<string, unknown> } }
  | { type: "tool_thinking_chunk"; payload: { chunk: string } }
  | { type: "chat_agent_thinking"; payload: { message: string } }
  | { type: "chat_agent_thinking_chunk"; payload: { chunk: string } }
  | { type: "explore_tool_call"; payload: { tool: string; args: Record<string, unknown>; thought?: string } }
  | { type: "intent_classified"; payload: { intent: string; rationale: string; likely_targets: string[] } }
  | { type: "chat_response"; payload: { chunk: string } }
  | { type: "chat_done"; payload: Record<string, never> }
  | { type: "task_card"; payload: { task_id: string } }
  | { type: "plan_card"; payload: { task_id: string; plan_markdown: string } }
  | { type: "task_status_changed"; payload: { task_id: string; status: string; plan_markdown?: string; message?: string } }
  | { type: "diff_ready"; payload: { task_id: string; diff_entries: DiffEntry[]; thinking_log: string[]; completed_steps: number; total_steps: number; resolved?: "applied" | "discarded" } }
  | { type: "thread_title_updated"; payload: { thread_id: string; title: string } }
  | { type: "step_review_requested"; payload: { step_id: string; step_title: string; diff_entries: DiffEntry[] } }
  | { type: "env_profile_building"; payload: { workspace_root: string } }
  | { type: "env_profile_built"; payload: { ecosystems_count: number; bootstrap_needed: boolean } }
  | { type: "env_install_running"; payload: { scope_key: string; command: string } }
  | { type: "env_install_done"; payload: { scope_key: string; exit_ok: boolean; tail: string } }
  | { type: "chat_breadcrumb"; payload: { text: string; task_id: string } }
  | { type: "chat_progress"; payload: { note: string } }
  | { type: "memory_compacted"; payload: { evicted: number; anchor_version: number } }
  | { type: "agent_started"; payload: { agent_id: string; parent_agent_id: string | null; depth: number; name: string; label: string } }
  | { type: "agent_status"; payload: { agent_id: string; status: string } }
  | { type: "agent_finished"; payload: { agent_id: string; status: string; files_changed: string[] } }
  | { type: "retry_status"; payload: { attempt: number; max_attempts: number; reason: string; message: string } }
  // Live token counts DURING a model call, ~6/sec. `thinking` climbs during
  // reasoning, then `output` climbs — the transition that otherwise looks like
  // a hang, since content deltas are accumulated silently until the call returns.
  // `input` is the prompt size, reported before the first delta — prefill produces
  // none, so without it the counter reads zero for most of a large call's wall time
  // and a slow turn is indistinguishable from a wedged one. `exact` is false while
  // the counts are chars/4 estimates and true once the provider's own usage lands.
  | {
      type: "token_progress";
      payload: {
        thinking: number;
        output: number;
        input: number | null;
        exact: boolean;
      };
    }
  // A failed edit has its own channel rather than chat_agent_thinking: a
  // preflight/engine error is not model reasoning and must not render as a
  // numbered reasoning step (same rule as retry_status).
  | { type: "edit_failed"; payload: { reason: string; ops: number } };

// Backward-compat alias
export type PatchStreamEvent = StreamEvent;

// ── Chat types ────────────────────────────────────────────────────────────

export const ChatMessageSchema = z.object({
  role: z.enum(["user", "agent"]),
  content: z.string(),
  // Stable rewind anchor. Absent/null on messages persisted before rewind shipped.
  id: z.string().nullable().optional(),
  type: z.enum(["text", "plan_card", "diff_card", "diff_summary", "task_card", "scope_card", "validation_card", "command_card", "agent_dispatch"]).default("text"),
  taskId: z.string().nullable().optional(),
  timestamp: z.string(),
  metadata: z.record(z.unknown()).default({}),
});
export type ChatMessage = z.infer<typeof ChatMessageSchema>;

// A sub-agent of a dispatch (spec §11.1, §11.3). `now`/`toolCount` exist only on /live
// (an in-flight turn); the routes' list view omits them.
export const AgentSummarySchema = z.object({
  agentId: z.string(),
  turnId: z.string().optional(),
  parentAgentId: z.string().nullable(),
  depth: z.number(),
  name: z.string(),
  label: z.string(),
  status: z.string(),
  now: z.string().default(""),
  toolCount: z.number().default(0),
  filesChangedCount: z.number(),
  startedAt: z.string().nullable(),
  endedAt: z.string().nullable(),
  reportPreview: z.string().default(""),
});
export type AgentSummary = z.infer<typeof AgentSummarySchema>;

export const AgentDetailSchema = AgentSummarySchema.extend({
  prompt: z.string(),
  report: z.string(),
  filesChanged: z.array(z.string()),
  staleRefusals: z.number(),
  transcript: z.array(ChatMessageSchema),
  lastSeq: z.number(),
});
export type AgentDetail = z.infer<typeof AgentDetailSchema>;

export const ChatThreadSummarySchema = z.object({
  threadId: z.string(),
  workspacePath: z.string(),
  title: z.string(),
  createdAt: z.string(),
  // Enriched list fields (chat UI v2 Tier A) -- optional so a bare summary
  // (e.g. POST /chat/threads response) still parses.
  updatedAt: z.string().optional(),
  messageCount: z.number().optional(),
  status: z.enum(["running", "review", "done", "failed"]).nullable().optional(),
});
export type ChatThreadSummary = z.infer<typeof ChatThreadSummarySchema>;

export const ChatThreadSchema = z.object({
  threadId: z.string(),
  workspacePath: z.string(),
  title: z.string(),
  messages: z.array(ChatMessageSchema),
  touchedFiles: z.array(z.string()),
});
export type ChatThread = z.infer<typeof ChatThreadSchema>;

// ── Chat rewind ───────────────────────────────────────────────────────────
// A rewind is destructive, so preview and execute are separate calls: the confirm
// dialog cannot be built from a request that already did the work.

export const RewindPreviewSchema = z.object({
  messages: z.number(),
  files: z.number(),
  commandsRun: z.number(),
  // Non-null means the POST will 409 — a task in the span is still running.
  blockedByTask: z.string().nullable().default(null),
  // Named, never killed: exec sessions are background shells.
  sessions: z.array(z.object({ id: z.string(), command: z.string() })).default([]),
});
export type RewindPreview = z.infer<typeof RewindPreviewSchema>;

export const RewindResultSchema = z.object({
  restoredFiles: z.array(z.string()).default([]),
  deletedFiles: z.array(z.string()).default([]),
  // Captured-as-too-large: reported as not restored rather than restored wrong.
  oversizeFiles: z.array(z.string()).default([]),
  failed: z.array(z.object({ path: z.string(), error: z.string() })).default([]),
  removedMessages: z.number(),
  prefillText: z.string().default(""),
  retiredMemories: z.number().default(0),
});
export type RewindResult = z.infer<typeof RewindResultSchema>;

export const ChatEventSchema = z.object({
  type: z.string(),
  payload: z.record(z.unknown()).default({}),
});
export type ChatEvent = z.infer<typeof ChatEventSchema>;

// Which sub-agent raised a gate (spec §4.5); null on the main agent's gates.
export const GateAgentSchema = z.object({
  id: z.string(),
  label: z.string(),
  name: z.string(),
});
export type GateAgent = z.infer<typeof GateAgentSchema>;

// One gate a thread is waiting on (mirrors backend PendingGate). A thread can hold
// several at once (spec §4.5), each addressed by gateId: a uuid for controller gates,
// "task:{taskId}:{kind}" for task-derived gates. "mode"/"edit"/"clarify"/"mcp_tool" are
// controller gates (no task) — the Zod enum is the RUNTIME gate: a kind missing here
// makes ThreadLiveStateSchema.parse() throw, which pollThreadLiveState swallows, so the
// gate silently never renders.
export const PendingGateSchema = z.object({
  gateId: z.string(),
  kind: z.enum(["command", "step", "scope", "validation", "mode", "edit", "clarify", "mcp_tool"]),
  payload: z.record(z.unknown()).default({}),
  agent: GateAgentSchema.nullable().default(null),
});
export type PendingGate = z.infer<typeof PendingGateSchema>;

// One item of the controller's live todo checklist (the write_todos ledger).
export const TodoItemSchema = z.object({
  title: z.string(),
  status: z.enum(["pending", "in_progress", "done", "blocked", "cancelled"]),
  note: z.string().optional().default(""),
});
export type TodoItem = z.infer<typeof TodoItemSchema>;

// One live exec session row on /live. STABLE by design (review fix #2):
// started_at instead of a ticking age_sec/unread_bytes — mutating fields here
// would flip the host's lastLiveSignature on every 1s poll and re-fire the
// whole render block at 1 Hz. The webview computes displayed age locally.
// Snake keys pass through unmapped (the memory-inspect signals convention).
export const SessionSummarySchema = z.object({
  id: z.string(),
  command: z.string(),
  status: z.enum(["running", "exited"]),
  exit_code: z.number().nullable(),
  started_at: z.number(),
});
export type SessionSummary = z.infer<typeof SessionSummarySchema>;

// The expandable PTY inspect payload (GET .../sessions/{id}/transcript).
// Served off an independent ring-buffer view — never advances the model cursor.
export const SessionTranscriptSchema = z.object({
  output_tail: z.string(),
  stdin_history: z.array(z.object({ ts: z.number(), chars: z.string() })),
  status: z.enum(["running", "exited"]),
  exit_code: z.number().nullable(),
});
export type SessionTranscript = z.infer<typeof SessionTranscriptSchema>;

// A thread's current actionable state — what the UI polls and renders from.
// Resolved server-side from the thread's active task (GET /chat/threads/{id}/live),
// so reloads and resume task-id churn self-heal on the next poll.
export const ThreadLiveStateSchema = z.object({
  activeTaskId: z.string().nullable(),
  status: z.string().nullable(),
  // Every pending gate, controller gates first (spec §4.5).
  pendingGates: z.array(PendingGateSchema).default([]),
  plan: z.record(z.unknown()).nullable(),
  // True while a controller turn / held-open controller gate is in flight (durable
  // input-disable signal that survives a webview reload). Absent on legacy payloads → false.
  turnActive: z.boolean().default(false),
  // Durable lifecycle telemetry (Tier B): drives the Error/Review cards from poll state.
  failureSummary: FailureSummarySchema.nullable().optional(),
  runSummary: RunSummarySchema.nullable().optional(),
  taskNarrative: TaskNarrativeSchema.nullable().optional(),
  todos: z.array(TodoItemSchema).nullable().optional(),
  sessions: z.array(SessionSummarySchema).nullable().optional(),
  agents: z.array(AgentSummarySchema).nullable().optional(),
});
export type ThreadLiveState = z.infer<typeof ThreadLiveStateSchema>;

export const ReasoningEffortSchema = z.enum(["off", "low", "medium", "high", "max"]);
export type ReasoningEffort = z.infer<typeof ReasoningEffortSchema>;

// Tri-state by omission: a rung in neither collection is UNKNOWN — the endpoint's
// capability is unverified, which the chip renders differently from "unsupported".
export const EffortSupportSchema = z.object({
  supported: z.array(ReasoningEffortSchema),
  unsupported: z.record(z.string(), z.string()),
});
export type EffortSupport = z.infer<typeof EffortSupportSchema>;

// Backend feature-flag capabilities (GET /v1/config) — drives task-path UI gating.
export const BackendConfigSchema = z.object({
  taskSubsystemEnabled: z.boolean(),
  chatControllerEnabled: z.boolean(),
  memoryEnabled: z.boolean(),
  skillsEnabled: z.boolean(),
  mcpEnabled: z.boolean(),
  subagentsEnabled: z.boolean().default(false),
  // Current reasoning provider (null when the backend runs scripted / pre-P4).
  // contextWindow is the window compaction is actually using right now — seeded
  // from CRUCIBLE_MEMORY_WINDOW_TOKENS at startup, overwritten by a settings save.
  provider: z.object({
    backend: z.string(),
    model: z.string(),
    contextWindow: z.number().nullable().optional(),
    reasoningEffort: ReasoningEffortSchema.nullable().optional(),
    reasoningEffortNote: z.string().nullable().optional(),
    reasoningEffortSupport: EffortSupportSchema.optional(),
  }).nullable().optional(),
});
export type BackendConfig = z.infer<typeof BackendConfigSchema>;

export const SkillSummarySchema = z.object({ name: z.string(), description: z.string() });
export type SkillSummary = z.infer<typeof SkillSummarySchema>;

// ── Settings surfaces (P4): provider validation + MCP server management.
// jsonMode/warning (Task 4, openai_compatible probe): jsonMode is undefined both
// when the backend was never probed (a known provider) and when the probe was
// inconclusive; `warning` disambiguates — see ProviderPingResult in validate.py.
export const ProviderValidateResultSchema = z.object({
  ok: z.boolean(),
  model: z.string().optional(),
  error: z.string().optional(),
  jsonMode: z.string().optional(),
  warning: z.string().optional(),
});
export type ProviderValidateResult = z.infer<typeof ProviderValidateResultSchema>;

// The context-window Test button's verdict. `ok` and `recalled` are separate on
// purpose: a provider can return HTTP 200 with an empty answer for an over-long
// prompt, so ok=true/recalled=false is the "your window is too big" case, not an
// error. `exact` says whether promptTokens came from the provider or from the
// backend's chars-per-token estimate.
export const ContextTestResultSchema = z.object({
  ok: z.boolean(),
  recalled: z.boolean(),
  promptTokens: z.number().optional(),
  exact: z.boolean().optional(),
  error: z.string().optional(),
});
export type ContextTestResult = z.infer<typeof ContextTestResultSchema>;

export const McpServerViewSchema = z.object({
  name: z.string(),
  transport: z.string(),
  enabledInFile: z.boolean(),
  state: z.string(),
  detail: z.string().nullable(),
  toolCount: z.number(),
});
export const McpServerListSchema = z.object({
  enabled: z.boolean(),
  servers: z.array(McpServerViewSchema),
});
export type McpServerView = z.infer<typeof McpServerViewSchema>;
export type McpServerList = z.infer<typeof McpServerListSchema>;

// ── Memory inspector (Phase 3-B). Read-only views of the recall trace + memory store.
// camelCase; the HttpBackendClient maps the snake_case route payloads into these.
export const RecallSignalsSchema = z.object({
  semantic: z.number(),
  lexical: z.number(),
  structural: z.number(),
  importance: z.number(),
  recency: z.number(),
});
export type RecallSignals = z.infer<typeof RecallSignalsSchema>;

export const RecallTraceEntrySchema = z.object({
  memoryId: z.string(),
  kind: z.string(),
  content: z.string(),
  importance: z.number(),
  signals: RecallSignalsSchema,
  fusedScore: z.number(),
  rerankScore: z.number().nullable(),
  finalRank: z.number(),
  injected: z.boolean(),
});
export type RecallTraceEntry = z.infer<typeof RecallTraceEntrySchema>;

export const RecallTraceSchema = z.object({
  query: z.string(),
  scopeKind: z.string(),
  scopeId: z.string(),
  k: z.number(),
  floor: z.number(),
  reranked: z.boolean(),
  entries: z.array(RecallTraceEntrySchema),
});
export type RecallTrace = z.infer<typeof RecallTraceSchema>;

export const MemoryViewSchema = z.object({
  id: z.string(),
  scopeKind: z.string(),
  scopeId: z.string(),
  kind: z.string(),
  content: z.string(),
  entities: z.array(z.string()),
  importance: z.number(),
  validFrom: z.string(),
  validTo: z.string().nullable(),
  supersededBy: z.string().nullable(),
  sourceKind: z.string(),
  sourceRef: z.string(),
  sourceSeqLo: z.number().nullable(),
  sourceSeqHi: z.number().nullable(),
  createdAt: z.string(),
});
export type MemoryView = z.infer<typeof MemoryViewSchema>;

export interface BackendTaskClient {
  submitTask(input: TaskSubmission): Promise<{ taskId: string }>;
  getTask(taskId: string): Promise<TaskView>;
  getTaskResult(taskId: string): Promise<TaskResult>;
  cancelTask(taskId: string): Promise<{ taskId: string; status: TaskStatus }>;
  // Cooperative Stop for a running task: revert rolls the workspace back, otherwise keeps
  // the changes applied so far (Tier B).
  abortTask(taskId: string, options: { revert: boolean }): Promise<TaskView>;
  // Live-mutable "Review each step" preference for a running task (Tier B).
  setReviewPref(taskId: string, options: { autoAccept: boolean }): Promise<TaskView>;
  // The same preference for an in-flight CHAT turn, where edits (not steps) are what
  // gets gated. Separate from setReviewPref because a controller turn has no task.
  setChatReviewPref(threadId: string, options: { autoAccept: boolean }): Promise<void>;
  acceptPatch(taskId: string): Promise<TaskResult>;
  rejectPatch(taskId: string, reason: string): Promise<TaskResult>;
  providePlanFeedback(taskId: string, feedback: string | null): Promise<TaskView>;
  resumeTask(taskId: string, options?: ResumeTaskRequest): Promise<ResumeTaskResponse>;
  sendScopeDecision(taskId: string, decision: ScopeDecisionRequest): Promise<ScopeDecisionResponse>;
  sendValidationDecision(taskId: string, decision: "accept" | "reject"): Promise<ValidationDecisionResponse>;
  sendCommandDecision(taskId: string, decision: CommandDecision): Promise<CommandDecisionResponse>;
  sendStepDecision(taskId: string, decision: "accept" | "discard"): Promise<void>;
  streamPatch(taskId: string, onEvent: (event: StreamEvent) => void, signal?: AbortSignal): Promise<void>;
  streamPatchEvents(taskId: string): AsyncIterable<StreamEvent>;
  listChatThreads(workspacePath: string): Promise<ChatThreadSummary[]>;
  createChatThread(workspacePath: string, title?: string): Promise<ChatThreadSummary>;
  getChatThread(threadId: string): Promise<ChatThread>;
  previewRewind(threadId: string, messageId: string): Promise<RewindPreview>;
  rewindThread(threadId: string, messageId: string): Promise<RewindResult>;
  getThreadLiveState(threadId: string): Promise<ThreadLiveState>;
  getSessionTranscript(threadId: string, sessionId: string): Promise<SessionTranscript>;
  getConfig(): Promise<BackendConfig>;
  getMemoryInspect(threadId: string): Promise<RecallTrace | null>;
  listMemories(filter: {
    scopeKind: string;
    scopeId: string;
    kind?: string;
    includeRetired?: boolean;
  }): Promise<MemoryView[]>;
  getSupersedeChain(memoryId: string): Promise<MemoryView[]>;
  listSkills(workspace: string): Promise<SkillSummary[]>;
  // Settings surfaces (P4): provider validation/hot-swap + MCP server management.
  validateProvider(req: { backend: string; model?: string; credentials?: Record<string, string> }): Promise<ProviderValidateResult>;
  setProvider(req: { backend: string; model?: string; credentials?: Record<string, string>; contextWindow?: number; reasoningEffort?: ReasoningEffort }): Promise<{ backend: string; model: string; reasoningEffort?: ReasoningEffort | null; reasoningEffortNote?: string | null; reasoningEffortSupport?: EffortSupport }>;
  testContextWindow(req: { backend: string; model?: string; credentials?: Record<string, string>; contextWindow: number }): Promise<ContextTestResult>;
  listMcpServers(): Promise<McpServerList>;
  upsertMcpServer(name: string, entry: Record<string, unknown>, disabled: string[]): Promise<McpServerList>;
  deleteMcpServer(name: string, disabled: string[]): Promise<McpServerList>;
  reconnectMcpServer(name: string, disabled: string[]): Promise<McpServerList>;
  sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: { stepReview?: boolean; forcedSkills?: string[]; mentionedFiles?: { path: string; content: string }[]; planMode?: boolean; messageId?: string }): AsyncIterable<StreamEvent>;
  // Controller gates (Phase F): the mode gate is a STREAMED dispatch (edit/create_task
  // produce live events); the per-edit gate is a plain JSON ack (its continuation rides
  // the already-open message stream).
  postModeDecision(threadId: string, mode: string): AsyncIterable<StreamEvent>;
  // Controller clarify gate: a STREAMED dispatch (the answer re-enters the loop).
  postClarifyDecision(threadId: string, answer: string): AsyncIterable<StreamEvent>;
  postEditDecision(threadId: string, decision: "accept" | "reject", reason?: string, gateId?: string): Promise<void>;
  // Controller run_command gate: a plain JSON ack (continuation rides the open message stream).
  // gateId addresses one of several pending gates; omitted = the single pending one.
  postChatCommandDecision(threadId: string, decision: CommandDecision, gateId?: string): Promise<void>;
  // Controller mcp_tool gate: a plain JSON ack (continuation rides the open message stream).
  postChatMcpDecision(threadId: string, decision: McpToolDecision, gateId?: string): Promise<void>;
  // Stop a detached controller turn (POST /chat/threads/{id}/stop). ok=false is benign.
  stopChatTurn(threadId: string): Promise<{ ok: boolean }>;
  // Sub-agents (spec §11.3).
  listAgents(threadId: string, turnId?: string): Promise<AgentSummary[]>;
  getAgent(threadId: string, agentId: string): Promise<AgentDetail>;
  stopAgent(threadId: string, agentId: string): Promise<{ ok: boolean }>;
  // Subscribe-only SSE to any broadcaster channel (GET /v1/channels/{id}/stream). Used
  // to resume the live overlay for a controller turn after a webview reload (chat:{id}).
  streamChannel(channelId: string): AsyncIterable<StreamEvent>;
  applyInlineChange(inlineTaskId: string): Promise<void>;
  discardInlineChange(inlineTaskId: string): Promise<void>;
}
