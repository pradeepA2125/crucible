import { isTerminalAgent } from "../../agents";
import type { AgentViewState } from "../../types";
import { MessageRow } from "../MessageRow";
import { AgentWorkTranscript } from "../shared/AgentWorkTranscript";
import { MarkdownContent } from "../shared/MarkdownContent";
import { useAgentsUi } from "./AgentsContext";

/** Changes whenever the view grows or a tool finishes — the follow-bottom trigger. */
export function viewToken(view: AgentViewState | undefined): string {
  if (!view) return "";
  return `${view.messages.length}:${view.live.length}:${view.live.filter((t) => t.done).length}`;
}

function SectionLabel({ children, color }: { children: string; color?: string }) {
  return (
    <div className="text-caption font-semibold tracking-[.08em]"
      style={{ color: color ?? "var(--color-text-4)" }}>
      {children}
    </div>
  );
}

function ReportBlock({ text, status }: { text: string; status: string }) {
  const ok = status === "completed";
  return (
    <div className="grid min-w-0 gap-2">
      <SectionLabel color={ok ? "var(--color-green)" : undefined}>
        {ok ? "REPORT" : `REPORT · ${status}`}
      </SectionLabel>
      <MarkdownContent content={text} />
    </div>
  );
}

/** One agent's history (spec §10): the task it was given, its transcript rendered with
 * the thread's grouped activity and prose, current work, and its full report. Shared by the
 * inline box and the floating window. */
export function AgentTranscript({ agentId }: { agentId: string }) {
  const { views, agents } = useAgentsUi();
  const view = views[agentId];
  if (!view) return <div className="text-meta text-text-3">Loading…</div>;
  const { detail } = view;
  const status = agents[agentId]?.status ?? detail.status;
  const hasReport = view.messages.some((m) => m.metadata?.report === true);
  return (
    <div className="mx-auto flex min-w-0 w-full max-w-[760px] flex-col gap-6 [&>*]:flex-shrink-0">
      <div className="grid min-w-0 gap-2">
        <SectionLabel>TASK FROM PARENT</SectionLabel>
        <div className="whitespace-pre-wrap [overflow-wrap:anywhere] rounded-card border border-border bg-surface px-3 py-2 text-body text-text-2">
          {detail.prompt}
        </div>
      </div>
      <AgentWorkTranscript key={agentId} messages={view.messages} live={view.live} running={!isTerminalAgent(status)}
        waiting={status === "waiting"} startedAt={agents[agentId]?.activationStartedAt ?? detail.activationStartedAt ?? detail.startedAt}
        renderMessage={m => m.metadata?.report === true
          ? <ReportBlock text={m.content} status={String(m.metadata?.status ?? status)} />
          : <MessageRow msg={m} />} />
      {!hasReport && isTerminalAgent(status) && detail.report !== "" && (
        <ReportBlock text={detail.report} status={status} />
      )}
    </div>
  );
}
