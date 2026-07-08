import type { TaskSessionState } from "./types.js";

export const SESSION_KEYS = {
  activeTaskId: "crucible.activeTaskId",
  backendBaseUrl: "crucible.backendBaseUrl",
  lastKnownStatus: "crucible.lastKnownStatus",
  workspacePath: "crucible.workspacePath",
  updatedAt: "crucible.updatedAt",
} as const;

export interface SessionStore {
  load(): Promise<TaskSessionState | null>;
  save(session: TaskSessionState): Promise<void>;
  clear(): Promise<void>;
}
