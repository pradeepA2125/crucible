import { Icon } from "../Icon";
import { elapsedMs, formatElapsed, isTerminalAgent } from "../../agents";
import type { AgentSummaryView } from "../../types";
import { AgentChip } from "./AgentChip";
import { useAgentsUi } from "./AgentsContext";
import { InlineAgentBox } from "./InlineAgentBox";
import { useNow } from "./useNow";

export type AgentTone = "done" | "running" | "waiting" | "failed" | "stopped";

export function toneOf(status: string): AgentTone {
  if (status === "completed" || status === "partial") return "done";
  if (status === "waiting") return "waiting";
  if (status === "failed") return "failed";
  // A stop is the user's choice, not a failure: its own (neutral) tally.
  if (status === "stopped") return "stopped";
  if (status === "awaiting_peer") return "stopped";      // idle, nothing wrong: neutral
  if (status === "failed_transient") return "waiting";   // the provider, not the agent: amber
  return "running";  // queued | running
}

export const TONE_COLOR: Record<AgentTone, string> = {
  done: "var(--color-green)",
  running: "var(--color-accent)",
  waiting: "var(--color-amber)",
  failed: "var(--color-red)",
  stopped: "var(--color-text-3)",
};

function firstLine(text: string): string {
  return text.split("\n").find((line) => line.trim())?.trim() ?? "";
}

export function statusLine(agent: AgentSummaryView): string {
  switch (agent.status) {
    case "queued": return "queued";
    case "waiting": return "⏸ needs your approval ↓";
    case "completed":
    case "partial": return `✓ reported: ${firstLine(agent.reportPreview)}`;
    case "failed": return `✗ ${firstLine(agent.reportPreview) || "failed"}`;
    case "stopped": return "■ stopped";
    case "awaiting_peer": return "⏳ waiting on a teammate";
    case "failed_transient": return "⚠ provider unavailable";
    default: return agent.now || "working…";
  }
}

function placeholder(agentId: string): AgentSummaryView {
  return {
    agentId, parentAgentId: null, depth: 1, name: "agent", label: "agent", status: "queued",
    now: "", toolCount: 0, filesChangedCount: 0, startedAt: null, endedAt: null, reportPreview: "",
  };
}

export function rosterRow(agents: Record<string, AgentSummaryView>, agentId: string): AgentSummaryView {
  return agents[agentId] ?? placeholder(agentId);
}

const TAGS: Array<{ tone: AgentTone; word: string; bg: string }> = [
  { tone: "done", word: "done", bg: "var(--green-bg)" },
  { tone: "running", word: "running", bg: "var(--accent-bg)" },
  { tone: "waiting", word: "waiting", bg: "var(--amber-bg)" },
  { tone: "failed", word: "failed", bg: "var(--red-bg)" },
  { tone: "stopped", word: "stopped", bg: "var(--color-surface-3)" },
];

/** The dispatch roster (spec §10): one row per agent with ▸ (inline) and ⤢ (window). */
export function AgentRosterCard({ agentIds }: { agentIds: string[] }) {
  const ui = useAgentsUi();
  const rows = agentIds.map((id) => rosterRow(ui.agents, id));
  const now = useNow(rows.some((a) => !isTerminalAgent(a.status)));
  const counts = new Map<AgentTone, number>();
  for (const a of rows) counts.set(toneOf(a.status), (counts.get(toneOf(a.status)) ?? 0) + 1);
  return (
    <div className="surface-card overflow-hidden" data-testid="agent-roster">
      <div className="accent-wash flex items-center gap-2 px-3 py-2"
        style={{ borderBottom: "1px solid var(--color-border)" }}>
        <span className="flex h-5 w-5 items-center justify-center rounded-md"
          style={{ background: "var(--accent-bg)", border: "1px solid var(--accent-brd)",
                   color: "var(--color-accent-ink)" }}>
          <Icon name="fork" size={11} />
        </span>
        <span className="text-xs font-semibold text-text">
          {rows.length} agent{rows.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto flex gap-1.5">
          {TAGS.filter((t) => counts.has(t.tone)).map((t) => (
            <span key={t.tone} className="rounded-full px-1.5 text-[10px]"
              style={{ background: t.bg, color: TONE_COLOR[t.tone] }}>
              {counts.get(t.tone)} {t.word}
            </span>
          ))}
        </span>
      </div>
      {rows.map((agent) => (
        <AgentRosterRow key={agent.agentId} agent={agent} now={now} siblings={agentIds} />
      ))}
    </div>
  );
}

/** One agent's live row outside its dispatch card — the resume line (spec §6) shows it
 * where the activity is, instead of the card far up the thread. */
export function AgentLiveRow({ agentId }: { agentId: string }) {
  const ui = useAgentsUi();
  const agent = rosterRow(ui.agents, agentId);
  const now = useNow(!isTerminalAgent(agent.status));
  return (
    <div className="surface-card overflow-hidden">
      <AgentRosterRow agent={agent} now={now} siblings={[agentId]} />
    </div>
  );
}

export function AgentRosterRow({ agent, now, siblings }: {
  agent: AgentSummaryView; now: number; siblings: string[];
}) {
  const ui = useAgentsUi();
  const open = ui.expanded.has(agent.agentId);
  const tone = toneOf(agent.status);
  const ms = elapsedMs(agent, now);
  const files = agent.filesChangedCount;
  const counts = `${agent.toolCount} tool${agent.toolCount === 1 ? "" : "s"}`
    + (files > 0 ? ` · ${files} file${files === 1 ? "" : "s"}` : "");
  return (
    <div className="[&:not(:last-child)]:border-b border-border">
      <div className="flex items-center gap-2 px-3 py-2"
        style={open ? { background: "var(--accent-bg)" } : undefined}>
        <span aria-label={agent.status} className="h-2 w-2 flex-shrink-0 rounded-full"
          style={{ background: TONE_COLOR[tone],
                   animation: tone === "running" ? "pulse 1.3s ease-in-out infinite" : undefined }} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-xs font-semibold text-text">{agent.label}</span>
            <AgentChip name={agent.name} />
          </div>
          <div className={tone === "waiting"
            ? "truncate text-[11px] text-amber"
            : "truncate font-mono text-[11px] text-text-3"}>
            {statusLine(agent)}
          </div>
        </div>
        <div className="whitespace-nowrap text-right text-[10.5px] leading-tight text-text-3">
          <div>{counts}</div>
          {ms !== null && <div>{formatElapsed(ms)}</div>}
        </div>
        <RowButton label={`${open ? "Collapse" : "Expand"} ${agent.label}`} active={open}
          onClick={() => ui.toggleExpanded(agent.agentId)}>
          <Icon name={open ? "chev-d" : "chev-r"} size={12} />
        </RowButton>
        <RowButton label={`Open ${agent.label} in a window`}
          onClick={() => ui.openWindow(agent.agentId, siblings)}>
          <Icon name="expand" size={11} />
        </RowButton>
      </div>
      {open && <InlineAgentBox agentId={agent.agentId} />}
    </div>
  );
}

function RowButton({ label, active, onClick, children }: {
  label: string; active?: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick}
      className="flex h-[22px] w-[22px] flex-shrink-0 cursor-pointer items-center justify-center rounded-md border transition-colors duration-150 hover:text-accent-ink"
      style={active
        ? { color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }
        : { color: "var(--color-text-3)", borderColor: "transparent" }}>
      {children}
    </button>
  );
}
