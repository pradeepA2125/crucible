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

export type TaskSubmission = z.infer<typeof TaskSubmissionSchema>;
export type TaskView = z.infer<typeof TaskViewSchema>;
export type TaskResult = z.infer<typeof TaskResultSchema>;
export type ResumeTaskRequest = z.infer<typeof ResumeTaskRequestSchema>;
export type ResumeTaskResponse = z.infer<typeof ResumeTaskResponseSchema>;

export type PatchStreamEvent =
  | { type: "operation_success"; op_type: string; path: string }
  | { type: "operation_error"; op_type: string; path: string; error: string }
  | { type: "done" };

export interface BackendTaskClient {
  submitTask(input: TaskSubmission): Promise<{ taskId: string }>;
  getTask(taskId: string): Promise<TaskView>;
  getTaskResult(taskId: string): Promise<TaskResult>;
  cancelTask(taskId: string): Promise<{ taskId: string; status: TaskStatus }>;
  acceptPatch(taskId: string): Promise<TaskResult>;
  rejectPatch(taskId: string, reason: string): Promise<TaskResult>;
  providePlanFeedback(taskId: string, feedback: string | null): Promise<TaskView>;
  resumeTask(taskId: string, options?: ResumeTaskRequest): Promise<ResumeTaskResponse>;
  streamPatch(taskId: string, onEvent: (event: PatchStreamEvent) => void, signal?: AbortSignal): Promise<void>;
}
