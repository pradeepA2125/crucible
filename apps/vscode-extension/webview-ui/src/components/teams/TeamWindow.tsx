import { useEffect, useState } from "react";
import { isTerminalTeam } from "../../teams";
import { vscode } from "../../vscodeApi";
import { Icon } from "../Icon";
import { AgentTranscript, viewToken } from "../agents/AgentTranscript";
import { TONE_COLOR, toneOf } from "../agents/AgentRosterCard";
import { useAgentsUi } from "../agents/AgentsContext";
import { useFollowBottom } from "../agents/useFollowBottom";
import { TeamBoard } from "./TeamBoard";
import { useTeamsUi } from "./TeamsContext";

interface Props {
  teamId: string;
  tab: string;            // "board" or a member's agentId
  onTab(tab: string): void;
  onClose(): void;
}

/** A team full height over the thread (spec v2 §9): Board first, one tab per member. */
export function TeamWindow({ teamId, tab, onTab, onClose }: Props) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const team = teamsUi.teams[teamId];
  const [confirming, setConfirming] = useState(false);
  const boardToken = String(teamsUi.views[teamId]?.lastSeq ?? 0);
  const { ref, onScroll } = useFollowBottom(
    `${teamId}|${tab}|${tab === "board" ? boardToken : viewToken(agentsUi.views[tab])}`);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!team) return null;
  const live = !isTerminalTeam(team.phase);
  return (
    <div role="presentation" className="scrim absolute inset-0 z-40"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}>
      <div role="dialog" aria-modal="true" aria-label={`Team ${team.name}`}
        className="surface-card anim-pop absolute inset-x-3 bottom-3 top-10 flex flex-col overflow-hidden">
        <div className="accent-wash px-3 pb-2 pt-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
          <div className="flex items-center gap-2">
            <Icon name="orbit" size={12} />
            <span className="truncate text-[13px] font-semibold text-text">{team.name}</span>
            <span className="whitespace-nowrap text-[10.5px] text-text-3">
              {team.phase.toLowerCase()} · round {team.round} of {team.maxRounds}
            </span>
            <span className="ml-auto flex items-center gap-1">
              {live && !confirming && (
                <button type="button" onClick={() => setConfirming(true)}
                  className="cursor-pointer rounded-md px-2 py-0.5 text-[10.5px]"
                  style={{ border: "1px solid var(--red-brd)", color: "var(--color-red)", background: "var(--red-bg)" }}>
                  Disband
                </button>
              )}
              {live && confirming && (
                <button type="button"
                  onClick={() => {
                    setConfirming(false);
                    vscode.postMessage({ type: "disbandTeam", teamId });
                  }}
                  className="cursor-pointer rounded-md px-2 py-0.5 text-[10.5px] font-semibold"
                  style={{ border: "1px solid var(--red-brd)", color: "var(--color-red)", background: "var(--red-bg)" }}>
                  Confirm disband
                </button>
              )}
              <button type="button" onClick={onClose} aria-label="Close team window" title="Close"
                className="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md text-text-3 transition-colors duration-150 hover:bg-surface-2 hover:text-text">
                <Icon name="x" size={12} />
              </button>
            </span>
          </div>
          <div className="mt-1 flex gap-2.5 text-[10.5px] text-text-3">
            <span className="truncate">{team.goal}</span>
            {team.usage.budget > 0 && <span className="whitespace-nowrap">budget {team.usage.budget}</span>}
          </div>
        </div>
        <div role="tablist" className="flex gap-0.5 overflow-x-auto px-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
          {[{ id: "board", label: "Board", status: null as string | null },
            // The member's own status until the roster has loaded its row.
            ...team.members.map((m) => ({ id: m.agentId, label: m.label,
              status: agentsUi.agents[m.agentId]?.status ?? m.status }))].map((t) => {
            const on = t.id === tab;
            return (
              <button key={t.id} type="button" role="tab" aria-selected={on} onClick={() => onTab(t.id)}
                className="flex cursor-pointer items-center gap-1.5 whitespace-nowrap border-b-2 px-2 py-1.5 text-[11px]"
                style={{ color: on ? "var(--color-accent-ink)" : "var(--color-text-3)",
                         borderColor: on ? "var(--color-accent)" : "transparent" }}>
                {t.status !== null && (
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: TONE_COLOR[toneOf(t.status)] }} />
                )}
                {t.label}
              </button>
            );
          })}
        </div>
        <div ref={ref} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
          {tab === "board" ? <TeamBoard teamId={teamId} /> : <AgentTranscript agentId={tab} />}
        </div>
      </div>
    </div>
  );
}
