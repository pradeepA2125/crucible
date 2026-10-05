import { useEffect, useState } from "react";
import { isTerminalAgent } from "../../agents";
import { isTerminalTeam } from "../../teams";
import { identityFor } from "../../teamIdentity";
import { vscode } from "../../vscodeApi";
import { Icon } from "../Icon";
import { viewToken } from "../agents/AgentTranscript";
import { TONE_COLOR, toneOf } from "../agents/AgentRosterCard";
import { useAgentsUi } from "../agents/AgentsContext";
import { useFollowBottom } from "../agents/useFollowBottom";
import { Avatar } from "./Avatar";
import { Journey } from "./Journey";
import { MemberView } from "./MemberView";
import { PhaseStepper } from "./PhaseStepper";
import { useTeamsUi } from "./TeamsContext";

interface Props {
  teamId: string;
  tab: string;            // "board" or a member's agentId
  onTab(tab: string): void;
  onClose(): void;
}

/** A team full height over the thread (spec 2026-10-05 §6–§7): a header with the road the
 * team travels and who is doing what, the Board first, then one tab per member. */
export function TeamWindow({ teamId, tab, onTab, onClose }: Props) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const team = teamsUi.teams[teamId];
  const [confirming, setConfirming] = useState(false);
  const view = teamsUi.views[teamId];
  const boardToken = `${view?.lastSeq ?? 0}:${view?.lastAseq ?? 0}`;
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
  const roster = team.members.map((m) => m.label);
  return (
    <div role="presentation" className="scrim absolute inset-0 z-40"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}>
      <div role="dialog" aria-modal="true" aria-label={`Team ${team.name}`}
        className="surface-card anim-pop absolute inset-x-3 bottom-3 top-10 flex flex-col overflow-hidden">
        <div className="accent-wash grid gap-2.5 px-3.5 pb-2.5 pt-3" style={{ borderBottom: "1px solid var(--color-border)" }}>
          <div className="flex flex-wrap items-center gap-2.5">
            <Avatar label="main" roster={roster} />
            <span className="truncate text-[14px] font-semibold text-text">{team.name}</span>
            <span className="min-w-0 flex-1 truncate text-[12px] text-text-2">{team.goal}</span>
            <span className="flex items-center gap-1">
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
          <PhaseStepper phase={team.phase} round={team.round} maxRounds={team.maxRounds} />
          <div className="flex flex-wrap items-center gap-3.5">
            {team.members.map((m) => {
              const status = agentsUi.agents[m.agentId]?.status ?? m.status;
              const working = !isTerminalAgent(status);
              return (
                <button key={m.agentId} type="button" aria-label={`Open ${m.label}'s tab`} onClick={() => onTab(m.agentId)}
                  className="flex cursor-pointer items-center gap-2 rounded-lg px-1 py-0.5 text-text-2 hover:bg-[var(--hairline)]">
                  <Avatar label={m.label} roster={roster} ring={working ? "working" : "idle"} />
                  <span className="grid text-left leading-tight">
                    <span className="text-[12px] font-semibold" style={{ color: identityFor(m.label, roster).color }}>{m.label}</span>
                    <small className="text-[10.5px] text-text-3">
                      {working ? "working" : status === "awaiting_peer" ? "waiting on a teammate" : "idle"}
                    </small>
                  </span>
                </button>
              );
            })}
            {team.usage.budget > 0 && <span className="ml-auto text-[11px] text-text-3">budget {team.usage.budget} requests</span>}
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
                {t.status !== null && <Avatar label={t.label} roster={roster} size="sm" />}
                {t.label}
                {t.status !== null && (
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: TONE_COLOR[toneOf(t.status)] }} />
                )}
              </button>
            );
          })}
        </div>
        <div ref={ref} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
          {tab === "board" ? <Journey teamId={teamId} /> : <MemberView teamId={teamId} agentId={tab} />}
        </div>
      </div>
    </div>
  );
}
