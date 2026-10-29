import { useState } from "react";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../shared/buttons";

interface Props {
  /** Carries the threadId (controller gates have no task — LiveSlot passes activeTaskId ?? threadId). */
  gateId: string;
  taskId: string;
  payload: Record<string, unknown>;
}

/**
 * McpGate — approval card for an external MCP tool call (kind="mcp_tool").
 * Copy is "Call MCP tool: server.tool" (NOT "Run command:") — spec decision 7.
 * Approve & remember persists the exact (server, tool) pair for this workspace.
 */
export function McpGate({ gateId, taskId, payload }: Props) {
  const server = String(payload.server ?? "");
  const tool = String(payload.tool ?? "");
  const nativeWeb = payload.native_web === true;
  const nativeFetch = payload.native_fetch === true;
  const args = (payload.args ?? {}) as Record<string, unknown>;
  const autoReason = typeof payload.auto_reason === "string" ? payload.auto_reason : "";
  const [resolved, setResolved] = useState<string | null>(null);

  function submit(approve: boolean, remember: boolean) {
    if (resolved !== null) return; // one-shot guard
    setResolved(approve ? (remember ? "Approved & remembered" : "Approved") : "Rejected");
    vscode.postMessage({ type: "mcpDecision", threadId: taskId, gateId, approve, remember });
  }

  return (
    <CardShell
      icon="term"
      title={nativeFetch ? "Fetch web page" : nativeWeb ? "Search the web with Exa" : `Call MCP tool: ${server}.${tool}`}
      subtitle={nativeFetch
        ? "The website receives this URL and your network address. Review the URL before approving."
        : nativeWeb
        ? "Your query is sent to Exa, which may use it for model training. Review it before approving."
        : "External MCP server — review the arguments before approving"}
      borderColor="var(--accent-brd)"
      headerTint="var(--accent-bg)"
    >
      {autoReason && <p className="px-2.5 py-2 border-t border-border text-meta text-text-2">Auto: {autoReason}</p>}
      <pre className="max-h-40 overflow-auto px-2.5 py-2 text-caption text-text-2 border-t border-border whitespace-pre-wrap">
        {JSON.stringify(args, null, 2)}
      </pre>
      {resolved === null ? (
        <div className="flex flex-wrap items-center gap-1.5 px-2.5 py-2 border-t border-border">
          <BtnPrimary onClick={() => submit(true, false)}>Approve once</BtnPrimary>
          <BtnGhost onClick={() => submit(true, true)}>
            Approve &amp; remember (this workspace)
          </BtnGhost>
          <BtnDanger onClick={() => submit(false, false)}>Reject</BtnDanger>
        </div>
      ) : (
        <div className="px-2.5 py-2 text-meta text-text-3 border-t border-border">{resolved}</div>
      )}
    </CardShell>
  );
}
