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
  "VALIDATING",
  "REPAIRING",
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

export const TaskViewSchema = z.object({
  taskId: z.string().min(1),
  status: TaskStatusSchema,
  goal: z.string().min(1),
  modifiedFiles: z.array(z.string()),
  diagnostics: DiagnosticsSchema,
  planMarkdown: z.string().optional(),
  resumeOfTaskId: z.string().optional()
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
  resumeOfTaskId: z.string().optional()
});

export const ResumeTaskRequestSchema = z.object({
  stage: z.enum(["plan", "feedback", "execute"]),
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

export const ScopeDecisionResponseSchema = z.object({
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

export type PatchStreamEvent =
  | { type: "operation_success"; op_type: string; path: string }
  | { type: "operation_error"; op_type: string; path: string; error: string }
  | { type: "scope_extension_requested"; decision_id: string; files: string[]; reason: string; step_id: string }
  | { type: "done" };

// ── Chat types ────────────────────────────────────────────────────────────

export const ChatMessageSchema = z.object({
  role: z.enum(["user", "agent"]),
  content: z.string(),
  type: z.enum(["text", "plan_card", "diff_card", "diff_summary"]).default("text"),
  taskId: z.string().nullable().optional(),
  timestamp: z.string(),
  metadata: z.record(z.unknown()).default({}),
});
export type ChatMessage = z.infer<typeof ChatMessageSchema>;

export const ChatThreadSummarySchema = z.object({
  threadId: z.string(),
  workspacePath: z.string(),
  title: z.string(),
  createdAt: z.string(),
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

export const ChatEventSchema = z.object({
  type: z.string(),
  payload: z.record(z.unknown()).default({}),
});
export type ChatEvent = z.infer<typeof ChatEventSchema>;

export interface BackendTaskClient {
  submitTask(input: TaskSubmission): Promise<{ taskId: string }>;
  getTask(taskId: string): Promise<TaskView>;
  getTaskResult(taskId: string): Promise<TaskResult>;
  cancelTask(taskId: string): Promise<{ taskId: string; status: TaskStatus }>;
  acceptPatch(taskId: string): Promise<TaskResult>;
  rejectPatch(taskId: string, reason: string): Promise<TaskResult>;
  providePlanFeedback(taskId: string, feedback: string | null): Promise<TaskView>;
  resumeTask(taskId: string, options?: ResumeTaskRequest): Promise<ResumeTaskResponse>;
  sendScopeDecision(taskId: string, decision: ScopeDecisionRequest): Promise<ScopeDecisionResponse>;
  streamPatch(taskId: string, onEvent: (event: PatchStreamEvent) => void, signal?: AbortSignal): Promise<void>;
  streamPatchEvents(taskId: string): AsyncIterable<PatchStreamEvent>;
  listChatThreads(workspacePath: string): Promise<ChatThreadSummary[]>;
  createChatThread(workspacePath: string, title?: string): Promise<ChatThreadSummary>;
  getChatThread(threadId: string): Promise<ChatThread>;
  sendChatMessage(threadId: string, message: string): AsyncIterable<ChatEvent>;
}
