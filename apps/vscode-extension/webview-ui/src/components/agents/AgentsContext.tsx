import { createContext, useContext } from "react";
import type { AgentSummaryView, AgentViewState } from "../../types";

/** What sub-agent UI needs from the thread: roster rows, open views and the two
 * affordances (inline ▸, floating ⤢). Provided by ThreadView. */
export interface AgentsUi {
  agents: Record<string, AgentSummaryView>;
  views: Record<string, AgentViewState>;
  expanded: ReadonlySet<string>;
  toggleExpanded(agentId: string): void;
  openWindow(agentId: string, siblings: string[]): void;
}

export const AgentsContext = createContext<AgentsUi>({
  agents: {}, views: {}, expanded: new Set(), toggleExpanded: () => {}, openWindow: () => {},
});

export function useAgentsUi(): AgentsUi {
  return useContext(AgentsContext);
}
