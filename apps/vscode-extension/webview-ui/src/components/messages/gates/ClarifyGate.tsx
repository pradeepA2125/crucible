import type { DraftPort } from "../../../drafts/types";
import { useGateDraft } from "../../../drafts/useGateDraft";
import { useDecisionRetry } from "../../shared/useDecisionRetry";
import { useRef, useState } from "react";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnGhost, BtnPrimary } from "../../shared/buttons";

interface Props {
  /** Carries the threadId (controller gates have no task — LiveSlot passes activeTaskId ?? threadId). */
  taskId: string;
  threadId?: string;
  gateId?: string;
  draftStore?: DraftPort;
  payload: Record<string, unknown>;
}

/** Parse the model-authored answer options — tolerant of missing/malformed entries. */
function parseOptions(payload: Record<string, unknown>): string[] {
  if (!Array.isArray(payload.options)) return [];
  return (payload.options as unknown[]).map((o) => String(o)).filter((s) => s.length > 0);
}

/**
 * ClarifyGate — the controller's clarify gate (sibling of ModeGate). Shows the agent's
 * question, model-authored candidate answers as one-click options, and an always-present
 * free-text "Something else…" escape. Picking either posts clarifyDecision; the backend
 * resolves the gate, writes a combined Q→A breadcrumb, and auto-resumes the agent.
 */
export function ClarifyGate({ taskId, threadId = taskId, gateId, draftStore, payload }: Props) {
  const question = String(payload.question ?? "");
  const options = parseOptions(payload);

  const [resolved, setResolved] = useState<string | null>(null);
  useDecisionRetry(threadId, "clarify", () => { pending.current = false; setResolved(null); }, gateId);
  const pending = useRef(false);
  const feedback = useGateDraft(draftStore, threadId, gateId ?? "", "clarify");
  const draft = feedback.text, setDraft = feedback.setText;
  const [error, setError] = useState("");

  async function submit(answer: string) {
    if (resolved !== null || pending.current || !gateId || !answer.trim()) return;
    if (answer.length > 65536) { setError("Keep your answer within 65,536 characters before sending."); return; }
    pending.current = true; setError("");
    try {
      const saved = await feedback.prepare();
      if (!feedback.isCurrent()) return; setResolved(answer);
      vscode.postMessage({ type: "gateDecision", threadId, gateId, kind: "clarify", value: answer, ...(saved && answer === draft ? { draft: saved } : {}) });
    } catch (error) { pending.current = false; setError(error instanceof Error ? error.message : "Answer could not be saved."); }
  }

  return (
    <CardShell
      icon="search"
      title="A quick question"
      subtitle={question || undefined}
      borderColor="var(--accent-brd)"
      headerTint="var(--accent-bg)"
    >
      {(error || feedback.notice) && <div role="status">{error || feedback.notice}</div>}
      {resolved === null ? (
        <div className="flex flex-col gap-1.5 px-2.5 py-2 border-t border-border">
          {options.map((opt) => (
            <BtnGhost key={opt} onClick={() => submit(opt)}>
              {opt}
            </BtnGhost>
          ))}
          {/* free-text escape — always present */}
          <div className="flex items-center gap-1.5 pt-1">
            <input
              className="flex-1 rounded-control border border-border bg-surface-2 px-2 py-1 text-meta text-text-1 outline-none"
              placeholder="Something else… (type your answer)"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit(draft);
              }}
            />
            <BtnPrimary onClick={() => submit(draft)}>Send</BtnPrimary>
          </div>
        </div>
      ) : (
        <div className="px-2.5 py-2 text-meta text-text-3 border-t border-border">
          Answered: {resolved}
        </div>
      )}
    </CardShell>
  );
}
