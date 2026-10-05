import { createContext, useContext } from "react";
import type { TeamSummaryView, TeamViewState } from "../../types";

/** What team UI needs from the thread (spec v2 §9). Provided by ThreadView. */
export interface TeamsUi {
  teams: Record<string, TeamSummaryView>;
  views: Record<string, TeamViewState>;
  openTeam(teamId: string): void;
}

export const TeamsContext = createContext<TeamsUi>({ teams: {}, views: {}, openTeam: () => {} });

export function useTeamsUi(): TeamsUi {
  return useContext(TeamsContext);
}
