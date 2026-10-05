import { isTerminalAgent } from "../../agents";
import { countsText, isTerminalTeam, latestText, memberPhrase } from "../../teams";
import { identityFor } from "../../teamIdentity";
import type { TeamSummaryView } from "../../types";
import { rosterRow } from "../agents/AgentRosterCard";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { Avatar } from "./Avatar";
import { PhaseStepper } from "./PhaseStepper";
import { useTeamsUi } from "./TeamsContext";

const TONE: Record<string, string> = {
  work: "var(--color-accent-ink)", ok: "var(--color-text-2)", wait: "var(--color-amber)",
  bad: "var(--color-red)", idle: "var(--color-text-3)",
};

function placeholder(teamId: string, name: string, agentIds: string[], labels: string[]): TeamSummaryView {
  return { teamId, name, goal: "", phase: "DELIBERATING", round: 1, maxRounds: 0, pausedReason: null,
    members: agentIds.map((agentId, i) => ({ label: labels[i], agentId, status: "queued" })),
    openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: "" };
}

/** A team in the transcript (spec 2026-10-05 §8), anchored by its team_created message. */
export function TeamCard({ teamId, agentIds, name = "team" }: {
  teamId: string; agentIds: string[]; name?: string;
}) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const team = teamsUi.teams[teamId]
    ?? placeholder(teamId, name, agentIds, agentIds.map((id) => rosterRow(agentsUi.agents, id).label));
  const roster = team.members.map((m) => m.label);
  const ended = isTerminalTeam(team.phase);
  const anyWorking = team.members.some((m) => !isTerminalAgent(agentsUi.agents[m.agentId]?.status ?? m.status));
  const now = useNow(!ended && anyWorking);
  const counts = countsText(team.counts, team.usage.budget);
  return (
    <div className="surface-card overflow-hidden" data-testid="team-card">
      <div className="accent-wash grid gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
        <div className="flex items-center gap-2">
          <Avatar label="main" roster={roster} size="sm" />
          <span className="truncate text-[13px] font-semibold text-text">{team.name}</span>
          <button type="button" onClick={() => teamsUi.openTeam(teamId)}
            className="ml-auto h-6 cursor-pointer whitespace-nowrap rounded-md border px-2.5 text-[11px] transition-transform duration-150 hover:-translate-y-px"
            style={{ borderColor: "var(--accent-brd)", color: "var(--color-accent-ink)", background: "var(--accent-bg)" }}>
            Open board
          </button>
        </div>
        {team.maxRounds > 0 && <PhaseStepper phase={team.phase} round={team.round} maxRounds={team.maxRounds} mini />}
        {team.pausedReason && <div className="text-[11px]" style={{ color: "var(--color-amber)" }}>{team.pausedReason}</div>}
      </div>
      {team.members.map((m) => {
        const row = agentsUi.agents[m.agentId];
        const phrase = memberPhrase(m, row, now);
        const working = phrase.tone === "work";
        return (
          <div key={m.agentId} data-testid={`member-row-${m.label}`}
            className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5 border-b px-3 py-2" style={{ borderColor: "var(--hairline)" }}>
            <Avatar label={m.label} roster={roster} ring={working ? "working" : "idle"} />
            <div className="min-w-0">
              <div className="flex items-baseline gap-1.5">
                <span className="text-[12px] font-semibold" style={{ color: identityFor(m.label, roster).color }}>{m.label}</span>
                {m.name && <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }}>{m.name}</span>}
              </div>
              <div className="truncate text-[11.5px]" style={{ color: TONE[phrase.tone] }}>{phrase.text}</div>
            </div>
            {row && <span className="whitespace-nowrap text-[10.5px] tabular-nums text-text-3">
              {row.activationCount ?? 0} ch · {row.toolCount} tools</span>}
          </div>
        );
      })}
      {team.latest && (
        <div className="flex min-w-0 items-center gap-2 px-3 pt-2 text-[11.5px] text-text-2">
          <Avatar label={team.latest.label} roster={roster} size="sm" />
          <span className="min-w-0 truncate">{latestText(team.latest)}</span>
        </div>
      )}
      {counts && <div className="px-3 pb-2.5 pt-1.5 text-[10.5px] text-text-3">{counts}</div>}
    </div>
  );
}
