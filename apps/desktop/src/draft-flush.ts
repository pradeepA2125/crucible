export { DraftFlusher } from "../../vscode-extension/src/drafts/draft-flush.js";

/** Shutdown accepts only the renderer saves needed by the pending flush. */
export function quitFlushCommand(type: string): boolean {
  return type === "draft/save" || type === "draft/flushAck" || type === "desktop/layoutSave";
}
/** Keep the attached draft round trip alive without reopening normal host publication. */
export function projectReplyAllowed(attached: boolean, closing: boolean, type: unknown): boolean {
  return attached && (!closing || type === "draft/result");
}
