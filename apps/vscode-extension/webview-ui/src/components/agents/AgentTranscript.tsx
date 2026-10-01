import { isTerminalAgent } from "../../agents";
import type { AgentViewState } from "../../types";
import { MessageRow } from "../MessageRow";
import { AgentRow } from "../messages/AgentRow";
import { MarkdownContent } from "../shared/MarkdownContent";
import { useAgentsUi } from "./AgentsContext";

/** Changes whenever the view grows or a pill finishes — the follow-bottom trigger. */
export function viewToken(view: AgentViewState | undefined): string {
  if (!view) return "";
  return `${view.messages.length}:${view.live.length}:${view.live.filter((t) => t.done).length}`;
}

function SectionLabel({ children, color }: { children: string; color?: string }) {
  return (
    <div className="text-[9.5px] font-semibold tracking-[.08em]"
      style={{ color: color ?? "var(--color-text-4)" }}>
      {children}
    </div>
  );
}

function ReportBlock({ text, status }: { text: string; status: string }) {
  const ok = status === "completed";
  return (
    <div className="flex flex-col gap-1">
      <SectionLabel color={ok ? "var(--color-green)" : undefined}>
        {ok ? "REPORT" : `REPORT · ${status}`}
      </SectionLabel>
      <div className="rounded-lg px-2.5 py-2 text-[11.5px] text-text-2"
        style={ok
          ? { border: "1px solid var(--green-brd)", background: "linear-gradient(180deg, var(--green-bg), transparent)" }
          : { border: "1px solid var(--color-border-strong)", background: "var(--color-surface)" }}>
        <MarkdownContent content={text} />
      </div>
    </div>
  );
}

/** One agent's history (spec §10): the task it was given, its transcript rendered with
 * the thread's own message components, live pills, and its full report. Shared by the
 * inline box and the floating window. */
export function AgentTranscript({ agentId }: { agentId: string }) {
  const { views } = useAgentsUi();
  const view = views[agentId];
  if (!view) return <div className="text-[11px] text-text-3">Loading…</div>;
  const { detail } = view;
  const hasReport = view.messages.some((m) => m.metadata?.report === true);
  return (
    <div className="flex flex-col gap-2 [&>*]:flex-shrink-0">
      <SectionLabel>TASK FROM PARENT</SectionLabel>
      <div className="whitespace-pre-wrap rounded-lg border border-border bg-surface px-2 py-1.5 text-[11.5px] text-text-2">
        {detail.prompt}
      </div>
      {view.messages.map((m, i) => (m.metadata?.report === true
        ? <ReportBlock key={i} text={m.content} status={String(m.metadata?.status ?? detail.status)} />
        : <MessageRow key={i} msg={m} />))}
      {view.live.length > 0 && <AgentRow content="" toolEvents={view.live} />}
      {!hasReport && isTerminalAgent(detail.status) && detail.report !== "" && (
        <ReportBlock text={detail.report} status={detail.status} />
      )}
    </div>
  );
}
