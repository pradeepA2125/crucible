import { isTerminalAgent } from "../../agents";
import { isTerminalTeam, roundProgressText, teamPhaseText } from "../../teams";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { Avatar } from "./Avatar";
import { useTeamsUi } from "./TeamsContext";

/** One line per live team, pinned above the composer: the board stays one click away
 * however far the conversation has moved past the team's card. Gone when the team ends. */
export function TeamStrip() {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const live = Object.values(teamsUi.teams).filter((t) => !isTerminalTeam(t.phase));
  const working = live.some((t) => t.members.some(
    (m) => !isTerminalAgent(agentsUi.agents[m.agentId]?.status ?? m.status)));
  const now = useNow(working);
  if (live.length === 0) return null;
  return (
    <div className="grid gap-1.5 px-3 pt-2">
      {live.map((team) => {
        const roster = team.members.map((m) => m.label);
        const progress = roundProgressText(team.roundProgress, team, agentsUi.agents, now);
        return (
          <div key={team.teamId} data-testid={`team-strip-${team.teamId}`}
            className="surface-card flex min-w-0 items-center gap-2 px-2.5 py-1.5 text-[11.5px]">
            <Avatar label="main" roster={roster} size="sm" />
            <span className="truncate font-semibold text-text">{team.name}</span>
            <span className="min-w-0 truncate text-text-2">· {progress?.text ?? teamPhaseText(team)}</span>
            <span className="ml-auto flex flex-none items-center gap-0.5">
              {team.members.map((m) => (
                <Avatar key={m.agentId} label={m.label} roster={roster} size="sm"
                  ring={isTerminalAgent(agentsUi.agents[m.agentId]?.status ?? m.status) ? "idle" : "working"} />
              ))}
            </span>
            <button type="button" onClick={() => teamsUi.openTeam(team.teamId)}
              aria-label={`Open board of ${team.name}`}
              className="h-6 flex-none cursor-pointer whitespace-nowrap rounded-md border px-2.5 text-[11px] transition-transform duration-150 hover:-translate-y-px"
              style={{ borderColor: "var(--accent-brd)", color: "var(--color-accent-ink)", background: "var(--accent-bg)" }}>
              Open board
            </button>
          </div>
        );
      })}
    </div>
  );
}
