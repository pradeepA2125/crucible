import type { ToolEventView } from "../../types";
import { teamForPill } from "../../teams";
import { useTeamsUi } from "./TeamsContext";

/** "Open board" for each team the main agent's team tool pills name (post_board,
 * team_status, adopt_proposal, create_team, …) — the way back to an ended team's board. */
export function TeamLinks({ events }: { events: ToolEventView[] }) {
  const teamsUi = useTeamsUi();
  const seen = new Map<string, string>();
  for (const event of events) {
    const team = teamForPill(event, teamsUi.teams);
    if (team !== null) seen.set(team.teamId, team.name);
  }
  if (seen.size === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1.5">
      {[...seen].map(([teamId, name]) => (
        <button key={teamId} type="button" aria-label={`Open board of ${name}`}
          onClick={() => teamsUi.openTeam(teamId)}
          className="rounded-full px-2 py-0.5 text-[10.5px] font-semibold"
          style={{ background: "var(--accent-bg)", color: "var(--color-accent-ink)" }}>
          Open board · {name}
        </button>
      ))}
    </div>
  );
}
