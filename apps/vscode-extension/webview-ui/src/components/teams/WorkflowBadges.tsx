import { TEAM_MODE_LABELS } from "../../teams";
import type { TeamWorkflowView } from "../../types";

export function WorkflowBadges({ team }: { team: TeamWorkflowView }) {
  if (!team.mode && !team.deliverable) return null;
  return <div className="flex flex-wrap items-center gap-1.5 text-[10.5px] text-text-2" aria-label="Team workflow">
    {team.mode && <span className="rounded-full border border-border px-2 py-0.5">{TEAM_MODE_LABELS[team.mode]}</span>}
    {team.deliverable && <span className="rounded-full border border-border px-2 py-0.5" title={team.deliverable.description}>
      Deliverable: {team.deliverable.kind}
    </span>}
  </div>;
}
