// Next enabled_tools/disabled_tools after toggling one tool's checkbox. A server
// entry with an allowlist keeps editing the allowlist, so a hand-written allowlist
// never silently becomes a blocklist that would expose tools the server adds later.

export interface ToolFilters {
  enabledTools: string[] | null;
  disabledTools: string[];
}

const sorted = (names: Iterable<string>): string[] => Array.from(new Set(names)).sort();

export function nextToolFilters(row: ToolFilters, tool: string, checked: boolean): ToolFilters {
  const disabled = new Set(row.disabledTools);
  if (checked) disabled.delete(tool);
  if (row.enabledTools === null) {
    if (!checked) disabled.add(tool);
    return { enabledTools: null, disabledTools: sorted(disabled) };
  }
  const enabled = new Set(row.enabledTools);
  if (checked) enabled.add(tool);
  else enabled.delete(tool);
  return { enabledTools: sorted(enabled), disabledTools: sorted(disabled) };
}
