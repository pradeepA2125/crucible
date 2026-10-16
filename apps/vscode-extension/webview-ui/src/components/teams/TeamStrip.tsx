import { useEffect, useRef, useState } from "react";
import { isTerminalAgent } from "../../agents";
import type { TeamSummaryView } from "../../types";
import { hasTeamExchange, isTerminalTeam, roundProgressText, teamPhaseText } from "../../teams";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { Icon } from "../Icon";
import { Avatar } from "./Avatar";
import { TeamCard } from "./TeamCard";
import { useTeamsUi } from "./TeamsContext";

/** Live teams above the composer, expandable into the same card used in the transcript.
 * Independent of main's turn/Worked disclosure; gone when the team's work ends. */
export function TeamStrip() {
  const teamsUi = useTeamsUi();
  const live = Object.values(teamsUi.teams).filter((t) => !isTerminalTeam(t.phase) || hasTeamExchange(t));
  if (live.length === 0) return null;
  return (
    <div className="mx-auto grid max-h-[40vh] min-w-0 w-full max-w-[760px] flex-shrink-0 gap-1.5 overflow-y-auto overscroll-contain px-4 pt-2">
      {live.map((team) => <LiveTeamTile key={team.teamId} team={team} />)}
    </div>
  );
}

function LiveTeamTile({ team }: { team: TeamSummaryView }) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const [expanded, setExpanded] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  const working = team.members.some(
    (m) => !isTerminalAgent(agentsUi.agents[m.agentId]?.status ?? m.status));
  const now = useNow(!expanded && working);
  const roster = team.members.map((m) => m.label);
  const progress = roundProgressText(team.roundProgress, team, agentsUi.agents, now);
  const contentId = `live-team-${team.teamId}`;
  useEffect(() => {
    if (restoreFocus.current) {
      toggleRef.current?.focus();
      restoreFocus.current = false;
    }
  }, [expanded]);
  const toggle = (
    <button ref={toggleRef} type="button" aria-expanded={expanded} aria-controls={contentId}
      aria-label={`${expanded ? "Minimize" : "Expand"} ${team.name}`}
      title={expanded ? "Minimize team" : "Expand team"}
      className="flex h-6 w-6 flex-none cursor-pointer items-center justify-center rounded-md text-text-2 hover:bg-surface"
      onClick={() => { restoreFocus.current = true; setExpanded(!expanded); }}>
      <Icon name={expanded ? "chev-d" : "expand"} size={14} />
    </button>
  );
  return (
    <div id={contentId} className="min-w-0" data-testid={`team-strip-${team.teamId}`}>
      {expanded ? (
        <TeamCard teamId={team.teamId} agentIds={team.members.map((m) => m.agentId)}
          headerActions={toggle} />
      ) : (
        <div
          className="surface-card flex min-w-0 items-center gap-2 px-2.5 py-1.5 text-[11.5px]">
          <Avatar label="main" roster={roster} size="sm" />
          <span className="min-w-0 max-w-[30%] truncate font-semibold text-text" title={team.name}>{team.name}</span>
          <span className="min-w-0 flex-1 truncate text-text-2">· {progress?.text ?? teamPhaseText(team)}</span>
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
          {toggle}
        </div>
      )}
    </div>
  );
}
