import { useState } from "react";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../shared/buttons";

interface Props {
  /** Carries the threadId (controller gates have no task — LiveSlot passes activeTaskId ?? threadId). */
  taskId: string;
  payload: Record<string, unknown>;
}

/**
 * McpGate — approval card for an external MCP tool call (kind="mcp_tool").
 * Copy is "Call MCP tool: server.tool" (NOT "Run command:") — spec decision 7.
 * Approve & remember persists the exact (server, tool) pair for this workspace.
 */
export function McpGate({ taskId, payload }: Props) {
  const server = String(payload.server ?? "");
  const tool = String(payload.tool ?? "");
  const args = (payload.args ?? {}) as Record<string, unknown>;
  const [resolved, setResolved] = useState<string | null>(null);

  function submit(approve: boolean, remember: boolean) {
    if (resolved !== null) return; // one-shot guard
    setResolved(approve ? (remember ? "Approved & remembered" : "Approved") : "Rejected");
    vscode.postMessage({ type: "mcpDecision", threadId: taskId, approve, remember });
  }

  return (
    <CardShell
      icon="term"
      title={`Call MCP tool: ${server}.${tool}`}
      subtitle="External MCP server — review the arguments before approving"
      borderColor="var(--accent-brd)"
      headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"
    >
      <pre className="max-h-40 overflow-auto px-2.5 py-2 text-[11px] text-text-2 border-t border-border whitespace-pre-wrap">
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
        <div className="px-2.5 py-2 text-[12px] text-text-3 border-t border-border">{resolved}</div>
      )}
    </CardShell>
  );
}
