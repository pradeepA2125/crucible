import { formatElapsed, isTerminalAgent } from "../../agents";
import { isTerminalTeam, waitingOn } from "../../teams";
import type { TeamSummaryView } from "../../types";
import { Icon } from "../Icon";
import { AgentRosterRow, rosterRow } from "../agents/AgentRosterCard";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { useTeamsUi } from "./TeamsContext";

function placeholder(teamId: string, name: string): TeamSummaryView {
  return { teamId, name, goal: "", phase: "DELIBERATING", round: 1, maxRounds: 0,
    pausedReason: null, members: [], openProposals: [], usage: { requests: 0, budget: 0 },
    createdAt: "" };
}

/** A team in the transcript (spec v2 §9), anchored by its team_created message. */
export function TeamCard({ teamId, agentIds, name = "team" }: {
  teamId: string; agentIds: string[]; name?: string;
}) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const team = teamsUi.teams[teamId] ?? placeholder(teamId, name);
  const rows = agentIds.map((id) => rosterRow(agentsUi.agents, id));
  const ended = isTerminalTeam(team.phase);
  const now = useNow(!ended && rows.some((a) => !isTerminalAgent(a.status)));
  const waiting = ended ? null : waitingOn(team, agentsUi.agents, now);
  return (
    <div className="surface-card overflow-hidden" data-testid="team-card">
      <div className="accent-wash px-3 py-2" style={{ borderBottom: "1px solid var(--color-border)" }}>
        <div className="flex items-center gap-2">
          <span className="flex h-5 w-5 items-center justify-center rounded-md"
            style={{ background: "var(--accent-bg)", border: "1px solid var(--accent-brd)",
                     color: "var(--color-accent-ink)" }}>
            <Icon name="orbit" size={11} />
          </span>
          <span className="truncate text-xs font-semibold text-text">{team.name}</span>
          <span className="whitespace-nowrap text-[10.5px] text-text-3">
            {team.phase.toLowerCase()} · round {team.round} of {team.maxRounds}
          </span>
          <button type="button" onClick={() => teamsUi.openTeam(teamId)}
            className="ml-auto cursor-pointer whitespace-nowrap rounded-md px-2 py-0.5 text-[10.5px]"
            style={{ border: "1px solid var(--accent-brd)", color: "var(--color-accent-ink)",
                     background: "var(--accent-bg)" }}>
            Open board
          </button>
        </div>
        {waiting && (
          <div className="mt-1 text-[11px] text-text-3">
            waiting on {waiting.label} ({formatElapsed(waiting.ms)})
          </div>
        )}
        {team.pausedReason && (
          <div className="mt-1 text-[11px] text-amber">{team.pausedReason}</div>
        )}
      </div>
      {rows.map((agent) => (
        <AgentRosterRow key={agent.agentId} agent={agent} now={now} siblings={agentIds} />
      ))}
      {team.usage.budget > 0 && (
        // Phase 4 counts no requests (spec v2 §8 owns the budget), so only the budget shows.
        <div className="px-3 py-1.5 text-[10.5px] text-text-3"
          style={{ borderTop: "1px solid var(--color-border)" }}>
          budget {team.usage.budget} requests
        </div>
      )}
    </div>
  );
}
