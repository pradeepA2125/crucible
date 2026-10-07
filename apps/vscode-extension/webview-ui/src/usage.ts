import { formatTokens } from "./statusWords";
import type { ThreadUsageView, TokenUsageView } from "./types";

/** Share of input tokens the provider served from its prompt cache, 0–100. */
export function cachedPercent(u: TokenUsageView): number {
  return u.input > 0 ? Math.round((u.cached / u.input) * 100) : 0;
}

/** "↑412k ↓9k · 38 req · 71% cached" — one shape everywhere usage appears. */
export function usageText(u: TokenUsageView): string {
  const parts = [`↑${formatTokens(u.input)} ↓${formatTokens(u.output)}`,
                 `${u.requests} req`];
  if (u.cached > 0) parts.push(`${cachedPercent(u)}% cached`);
  return parts.join(" · ");
}

export function usageTitle(u: TokenUsageView): string {
  return `${u.requests} requests · ${u.input.toLocaleString()} input tokens `
    + `(${u.cached.toLocaleString()} cached) · ${u.output.toLocaleString()} output tokens`;
}

function minus(a: TokenUsageView, b: TokenUsageView): TokenUsageView {
  return { requests: Math.max(0, a.requests - b.requests), input: Math.max(0, a.input - b.input),
           output: Math.max(0, a.output - b.output), cached: Math.max(0, a.cached - b.cached) };
}

function plus(a: TokenUsageView, b: TokenUsageView): TokenUsageView {
  return { requests: a.requests + b.requests, input: a.input + b.input,
           output: a.output + b.output, cached: a.cached + b.cached };
}

const ZERO: TokenUsageView = { requests: 0, input: 0, output: 0, cached: 0 };

export interface UsageRow { key: string; label: string; usage: TokenUsageView }

/** The composer's breakdown: the main agent, each team, then every other agent together
 * (agents not working for a team). Rows with no requests are left out. */
export function threadBreakdown(
  usage: ThreadUsageView, teamNames: Record<string, string>,
): UsageRow[] {
  const rows: UsageRow[] = [];
  if (usage.main) rows.push({ key: "main", label: "Main agent", usage: usage.main });
  let inTeams = ZERO;
  for (const [teamId, team] of Object.entries(usage.teams)) {
    inTeams = plus(inTeams, team.total);
    rows.push({ key: `team:${teamId}`, label: `Team ${teamNames[teamId] ?? teamId}`, usage: team.total });
  }
  const allAgents = Object.values(usage.agents).reduce(plus, ZERO);
  rows.push({ key: "agents", label: "Other agents", usage: minus(allAgents, inTeams) });
  return rows.filter((r) => r.usage.requests > 0);
}
