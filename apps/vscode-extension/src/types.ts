import type { TaskStatus } from "@ai-editor/editor-client";

export type TaskMode = "inline" | "file_edit" | "project_edit" | "autonomous";

export interface TaskSessionState {
  taskId: string;
  status: TaskStatus;
  workspacePath: string;
  backendBaseUrl: string;
  updatedAt: string;
}

export interface ReviewFileEntry {
  relativePath: string;
  realPath: string;
  shadowPath: string;
  existsReal: boolean;
  existsShadow: boolean;
}
