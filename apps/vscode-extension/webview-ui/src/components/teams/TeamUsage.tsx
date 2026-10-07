import { identityFor } from "../../teamIdentity";
import type { TeamSummaryView } from "../../types";
import { usageText, usageTitle } from "../../usage";
import { Avatar } from "./Avatar";
import { useTeamsUi } from "./TeamsContext";

/** The board's token breakdown: the team total, then each member (with its helpers). */
export function TeamUsage({ team, onMember }: {
  team: TeamSummaryView; onMember(agentId: string): void;
}) {
  const usage = useTeamsUi().usage?.teams[team.teamId];
  if (!usage || usage.total.requests === 0) return null;
  const roster = team.members.map((m) => m.label);
  return (
    <section data-testid="team-usage" className="mb-3 rounded-[10px] border border-border bg-surface px-3 py-2">
      <div className="flex items-baseline gap-2">
        <span className="text-[10px] uppercase tracking-[.08em] text-text-3">Token usage</span>
        <span className="ml-auto font-mono text-[10.5px] tabular-nums text-text-2"
          title={usageTitle(usage.total)}>{usageText(usage.total)}</span>
      </div>
      <div className="mt-1.5 grid gap-0.5">
        {team.members.map((m) => {
          const mine = usage.members[m.label];
          return (
            <button key={m.agentId} type="button" data-testid={`member-usage-${m.label}`}
              onClick={() => onMember(m.agentId)}
              title={mine ? `${usageTitle(mine)} (includes its helpers)` : undefined}
              className="grid cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 rounded-md border-0 bg-transparent px-1 py-0.5 text-left hover:bg-[var(--hairline)]">
              <Avatar label={m.label} roster={roster} size="sm" />
              <span className="truncate text-[11px] font-semibold"
                style={{ color: identityFor(m.label, roster).color }}>{m.label}</span>
              <span className="font-mono text-[10.5px] tabular-nums text-text-3">
                {mine && mine.requests > 0 ? usageText(mine) : "—"}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
