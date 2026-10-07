import { useEffect } from "react";
import { elapsedMs, formatElapsed, isTerminalAgent } from "../../agents";
import { vscode } from "../../vscodeApi";
import { Icon } from "../Icon";
import { AgentChip } from "./AgentChip";
import { AgentTranscript, viewToken } from "./AgentTranscript";
import { TONE_COLOR, rosterRow, toneOf } from "./AgentRosterCard";
import { useAgentsUi } from "./AgentsContext";
import { useFollowBottom } from "./useFollowBottom";
import { useNow } from "./useNow";
import { usageText, usageTitle } from "../../usage";

interface Props {
  agentId: string;
  siblings: string[];
  onSwitch(agentId: string): void;
  onClose(): void;
}

/** ⤢ — one agent full height over the thread (spec §10): meta, ■ Stop, sibling tabs.
 * One window at a time; Esc, ✕ or a backdrop click closes it. */
export function AgentWindow({ agentId, siblings, onSwitch, onClose }: Props) {
  const ui = useAgentsUi();
  const agent = rosterRow(ui.agents, agentId);
  const running = !isTerminalAgent(agent.status);
  const now = useNow(running);
  const ms = elapsedMs(agent, now);
  const tokens = ui.usage?.agents[agentId];
  const { ref, onScroll } = useFollowBottom(`${agentId}|${viewToken(ui.views[agentId])}`);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div role="presentation" className="scrim absolute inset-0 z-40"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}>
      <div role="dialog" aria-modal="true" aria-label={`Sub-agent ${agent.label}`}
        className="surface-card anim-pop absolute inset-x-3 bottom-3 top-10 flex flex-col overflow-hidden">
        <div className="accent-wash px-3 pb-2 pt-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 flex-shrink-0 rounded-full" style={{ background: TONE_COLOR[toneOf(agent.status)] }} />
            <span className="truncate text-[13px] font-semibold text-text">{agent.label}</span>
            <AgentChip name={agent.name} />
            <span className="ml-auto flex items-center gap-1">
              {running && (
                <button type="button"
                  onClick={() => vscode.postMessage({ type: "stopAgent", agentId })}
                  className="cursor-pointer rounded-md px-2 py-0.5 text-[10.5px]"
                  style={{ border: "1px solid var(--red-brd)", color: "var(--color-red)", background: "var(--red-bg)" }}>
                  ■ Stop
                </button>
              )}
              <button type="button" onClick={onClose} aria-label="Close agent window" title="Close"
                className="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md text-text-3 transition-colors duration-150 hover:bg-surface-2 hover:text-text">
                <Icon name="x" size={12} />
              </button>
            </span>
          </div>
          <div className="mt-1 flex gap-2.5 text-[10.5px] text-text-3">
            <span><b className="font-semibold text-text-2">{agent.status}</b>{ms !== null ? ` · ${formatElapsed(ms)}` : ""}</span>
            <span><b className="font-semibold text-text-2">{agent.toolCount}</b> tools</span>
            <span><b className="font-semibold text-text-2">{agent.filesChangedCount}</b> file{agent.filesChangedCount === 1 ? "" : "s"} changed</span>
            <span>depth {agent.depth}</span>
            {tokens && tokens.requests > 0 && (
              <span data-testid="agent-tokens" className="ml-auto font-mono tabular-nums"
                title={usageTitle(tokens)}>{usageText(tokens)}</span>)}
          </div>
        </div>
        {siblings.length > 1 && (
          <div role="tablist" className="flex gap-0.5 px-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
            {siblings.map((id) => {
              const sib = rosterRow(ui.agents, id);
              const on = id === agentId;
              return (
                <button key={id} type="button" role="tab" aria-selected={on} onClick={() => onSwitch(id)}
                  className="flex cursor-pointer items-center gap-1.5 border-b-2 px-2 py-1.5 text-[11px]"
                  style={{ color: on ? "var(--color-accent-ink)" : "var(--color-text-3)",
                           borderColor: on ? "var(--color-accent)" : "transparent" }}>
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: TONE_COLOR[toneOf(sib.status)] }} />
                  {sib.label}
                </button>
              );
            })}
          </div>
        )}
        <div ref={ref} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
          <AgentTranscript agentId={agentId} />
        </div>
      </div>
    </div>
  );
}
