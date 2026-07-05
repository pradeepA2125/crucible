export type McpTransport = "stdio" | "http" | "sse";

// Mirrors src/mcp-quickpick.ts::splitCommandLine — a bare \s+ split breaks on
// any quoted argument containing a space (e.g. a path under a directory named
// "AI editor") — this repo's own path is a real case. Supports "double" and
// 'single' quoted tokens; unquoted tokens split on whitespace as before.
export function splitCommandLine(input: string): string[] {
  const tokens: string[] = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(input)) !== null) {
    const token = match[1] ?? match[2] ?? match[3] ?? "";
    if (token) tokens.push(token);
  }
  return tokens;
}

// Mirrors src/mcp-quickpick.ts::buildMcpEntry (Task 14) — same assembly rules, a
// separate call site (webview form vs. QuickPick command).
export function buildMcpEntry(input: {
  transport: McpTransport;
  commandLine: string;
  url: string;
  envVarNames: string[];
}): Record<string, unknown> {
  if (input.transport === "stdio") {
    const [command, ...args] = splitCommandLine(input.commandLine.trim());
    const entry: Record<string, unknown> = { command, args, enabled: true };
    if (input.envVarNames.length) {
      entry.env = Object.fromEntries(input.envVarNames.map((v) => [v, `\${${v}}`]));
    }
    return entry;
  }
  const entry: Record<string, unknown> = { type: input.transport, url: input.url, enabled: true };
  if (input.envVarNames.length) {
    const [first, ...rest] = input.envVarNames;
    const headers: Record<string, string> = { Authorization: `Bearer \${${first}}` };
    for (const name of rest) headers[name] = `\${${name}}`;
    entry.headers = headers;
  }
  return entry;
}
