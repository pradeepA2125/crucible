import { useEffect, useRef, useState } from "react";
import { vscode } from "../vscodeApi";
import { useModalFocus } from "./shared/useModalFocus";

type Choice = "continue" | "accept_edits" | "cancel";
interface Question { requestId: string; workspace: string }

/** The host asks before the first message under Full access in a workspace (the saved mode is
 * global, so it can arrive from elsewhere). The app owns the dialog so any host can reuse it;
 * the host keeps the guard and refuses the send unless the answer is Continue or Switch. */
export function FullAccessFirstSendDialog() {
  const [question, setQuestion] = useState<Question | null>(null);
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const m = e.data;
      if (m?.type === "confirmFullAccessFirstSend" && typeof m.requestId === "string") {
        setQuestion({ requestId: m.requestId, workspace: typeof m.workspace === "string" ? m.workspace : "this workspace" });
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);
  if (!question) return null;
  const answer = (choice: Choice) => {
    vscode.postMessage({ type: "fullAccessFirstSendChoice", requestId: question.requestId, choice });
    setQuestion(null);
  };
  return <Dialog workspace={question.workspace} onAnswer={answer} />;
}

function Dialog({ workspace, onAnswer }: { workspace: string; onAnswer(choice: Choice): void }) {
  const root = useRef<HTMLDivElement>(null);
  const cancel = () => onAnswer("cancel");
  useModalFocus(root, cancel);
  return (
    <div className="scrim fixed inset-0 z-[70] flex items-center justify-center p-4" onClick={e => { if (e.target === e.currentTarget) cancel(); }}>
      <div ref={root} role="dialog" aria-modal="true" aria-labelledby="first-send-title" aria-describedby="first-send-body" tabIndex={-1}
        className="grid w-full max-w-[400px] gap-3 rounded-card border border-border-strong bg-surface-3 p-4 text-body text-text">
        <h2 id="first-send-title" className="font-semibold">Full access is on in this workspace</h2>
        <div id="first-send-body" className="grid gap-2">
          <p>Crucible will apply edits and run commands in {workspace} without asking. Commands can change or delete files and run programs.</p>
          <p className="text-meta text-text-2">Protected paths, untrusted-agent edits and MCP tools still ask. You’ll only see this once here.</p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={() => onAnswer("accept_edits")} className="rounded-control border border-border px-3 py-1.5 hover:bg-surface-2">Switch to Accept edits</button>
          <button type="button" onClick={cancel} className="rounded-control border border-border px-3 py-1.5 hover:bg-surface-2">Cancel</button>
          <button type="button" onClick={() => onAnswer("continue")} className="rounded-control bg-[var(--accent-fill)] px-3 py-1.5 text-[var(--on-accent)] hover:bg-[var(--accent-fill-hover)]">Continue with Full access</button>
        </div>
      </div>
    </div>
  );
}
