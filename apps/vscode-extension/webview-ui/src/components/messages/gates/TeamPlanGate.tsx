import { useState } from "react";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../shared/buttons";

interface Props {
  gateId: string;
  /** The thread id — a team's card belongs to the thread, not a task. */
  taskId: string;
  payload: Record<string, unknown>;
}

interface Assignment { member: string; part: string; files: string[] }

function assignmentsOf(payload: Record<string, unknown>): Assignment[] {
  const raw = Array.isArray(payload.assignments) ? payload.assignments : [];
  return raw.filter((a): a is Record<string, unknown> => typeof a === "object" && a !== null)
    .map((a) => ({ member: String(a.member ?? ""), part: String(a.part ?? ""),
                   files: Array.isArray(a.files) ? a.files.map(String) : [] }));
}

/** The user's approval of a team's adopted plan (spec v2 §8.5): approve, send feedback for
 * one more round, or reject (the team ends). One decision per card. */
export function TeamPlanGate({ gateId, taskId, payload }: Props) {
  const [resolved, setResolved] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const team = String(payload.team_name ?? "");
  const proposal = String(payload.proposal_id ?? "");
  const shared = Array.isArray(payload.shared_files) ? payload.shared_files.map(String) : [];

  function decide(decision: "approve" | "feedback" | "reject") {
    if (resolved !== null) return;
    const text = feedback.trim();
    if (decision === "feedback" && text === "") return;
    setResolved({ approve: "Approved", feedback: "Feedback sent", reject: "Rejected" }[decision]);
    vscode.postMessage({ type: "teamPlanDecision", threadId: taskId, gateId, decision,
                         ...(decision === "feedback" ? { feedback: text } : {}) });
  }

  return (
    <CardShell
      icon="check"
      title={`Team ${team} adopted ${proposal} — approve the plan?`}
      subtitle="The members implement it after you approve"
      borderColor="var(--accent-brd)"
      headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"
    >
      <div className="max-h-60 overflow-auto border-t border-border px-2.5 py-2 text-[12px] text-text-2">
        <p className="whitespace-pre-wrap">{String(payload.text ?? "")}</p>
        <ul className="mt-2 grid gap-0.5">
          {assignmentsOf(payload).map((a) => (
            <li key={a.member}>
              <span className="font-semibold text-text">{a.member} → {a.part}</span>
              {a.files.length > 0 && <span className="text-text-3"> ({a.files.join(", ")})</span>}
            </li>
          ))}
        </ul>
        {shared.length > 0 && <p className="mt-1 text-text-3">Shared: {shared.join(", ")}</p>}
      </div>
      {resolved === null ? (
        <div className="grid gap-1.5 border-t border-border px-2.5 py-2">
          <textarea aria-label="Feedback for the team" rows={2} value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="What should the team change? (sends the plan back for another round)"
            className="w-full resize-y rounded border border-border bg-panel px-2 py-1 text-[12px]" />
          <div className="flex flex-wrap items-center gap-1.5">
            <BtnPrimary onClick={() => decide("approve")}>Approve</BtnPrimary>
            <BtnGhost onClick={() => decide("feedback")}>Send feedback</BtnGhost>
            <BtnDanger onClick={() => decide("reject")}>Reject</BtnDanger>
          </div>
        </div>
      ) : (
        <div className="border-t border-border px-2.5 py-2 text-[12px] text-text-3">{resolved}</div>
      )}
    </CardShell>
  );
}
