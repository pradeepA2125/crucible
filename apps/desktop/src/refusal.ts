import { z } from "zod";
import { baselinePath, parseBaselineReply } from "./baseline-protocol.js";
/** Preserve existing React recovery semantics when the desktop boundary refuses an intent. */
export function refusalMessages(raw: unknown, message: string, projectId: string | null = null, context?: {viewId:string;threadId:string|null}): Record<string, unknown>[] {
  const payload = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
  const type = typeof payload.type === "string" ? payload.type : "";
  if(type==="desktop/restore"){
    if(!context||!z.string().uuid().safeParse(payload.requestId).success)return[];
    return[{type:"desktop/restoreResult",requestId:payload.requestId,...context,projectId,error:message.slice(0,1024)}];
  }
  if (type.startsWith("desktop/baseline")) {
    // Malformed requests still receive a correlated transport refusal when their
    // correlation fields are valid; never trigger chat input/draft recovery.
    if (!context || !z.string().uuid().safeParse(payload.requestId).success) return [];
    const operation = type === "desktop/baselineList" ? "list" : type === "desktop/baselineScope" ? "scope" : type === "desktop/baselineDiff" ? "diff" : undefined;
    if (!operation) return [];
    const detail = operation === "diff" ? z.object({generation:z.string().uuid(),path:baselinePath}).safeParse(payload) : undefined;
    if (detail && !detail.success) return [];
    return [parseBaselineReply({type:"desktop/baselineResult",requestId:payload.requestId,projectId,viewId:context.viewId,threadId:context.threadId,operation,
      ...(detail?.success ? detail.data : {}),outcome:{status:"error",message:"Checkpoint inspection was refused. Refresh after the view reconnects."}},operation)];
  }
  if (type === "desktop/update") return [{type:"desktop/updateStatus",state:{phase:"error",currentVersion:"",message,canDownload:false,canInstall:false,backupAvailable:false}}];
  if (type.startsWith("desktop/lifecycle")) return [{type:"desktop/lifecycleError",message}];
  if (type === "desktop/terminal" && typeof payload.requestId === "string") return [{type:"desktop/terminalResult",requestId:payload.requestId,error:message}];
  if (type.startsWith("desktop/git")) return typeof payload.requestId === "string" ? [{ type: "desktop/gitResult", requestId: payload.requestId, projectId,
    operation: type === "desktop/gitScope" ? "scope" : "diff", outcome: { status: "error", message: message.slice(0, 2048) || "Git inspection was refused." } }] : [];
  if (type.startsWith("desktop/threads") && typeof payload.requestId === "string") return [{ type: "desktop/threadsResult", requestId: payload.requestId, error: message }];
  if (type.startsWith("desktop/files") && typeof payload.requestId === "string") return [{ type: "desktop/filesResult", requestId: payload.requestId, error: message }];
  if (type.startsWith("desktop/layout") && typeof payload.requestId === "string") return [{ type: "desktop/layoutResult", requestId: payload.requestId, error: message }];
  if (type.startsWith("draft/") && typeof payload.requestId === "string") return [{ type: "draft/result", requestId: payload.requestId, error: message }];
  if (["checkChatReceipt", "setOpenAgents", "setOpenTeams", "listModels", "listPrompts", "listSkills", "listWorkspaceFiles", "requestFileReferenceCatalog", "fetchSessionTranscript", "webviewReady", "themeReady"].includes(type)) return [];
  if (type === "gateDecision") return [{ type: "decisionRetry", threadId: payload.threadId, gateId: payload.gateId,
    kind: payload.kind === "feedback" ? "mode" : payload.kind }, { type: "gateDecisionNotice", threadId: payload.threadId, gateId: payload.gateId, message }];
  const out: Record<string, unknown>[] = [{ type: "setInputEnabled", enabled: true }];
  if (type === "sendMessage") out.push({ type: "draftRecovery",
    text: typeof payload.originalDraftText === "string" ? payload.originalDraftText : typeof payload.text === "string" ? payload.text : "",
    draftId: payload.draftId, imageAttachments: payload.imageAttachments ?? [], mentionedPaths: payload.mentionedPaths ?? [],
  });
  const kind = ({ modeDecision: "mode", clarifyDecision: "clarify", teamPlanDecision: "team_plan" } as Record<string, string>)[type];
  if (kind) out.push({ type: "decisionRetry", threadId: payload.threadId, kind, gateId: payload.gateId });
  if (type.startsWith("setup/")) out.push({ type: "setup/error", message });
  else if (type.startsWith("settings/")) out.push({ type: "settings/error", message });
  else out.push({ type: "appendMessage", message: { role: "agent", type: "notice", content: message, timestamp: new Date().toISOString(), metadata: {} } });
  return out;
}
