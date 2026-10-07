import { createContext, useContext } from "react";
import type { TeamSummaryView, TeamViewState, ThreadUsageView } from "../../types";

/** What team UI needs from the thread (spec v2 §9). Provided by ThreadView. */
export interface TeamsUi {
  teams: Record<string, TeamSummaryView>;
  views: Record<string, TeamViewState>;
  // The thread's token usage: each team's total and its members' (null until loaded).
  usage?: ThreadUsageView | null;
  openTeam(teamId: string): void;
}

export const TeamsContext = createContext<TeamsUi>({ teams: {}, views: {}, usage: null, openTeam: () => {} });

export function useTeamsUi(): TeamsUi {
  return useContext(TeamsContext);
}
