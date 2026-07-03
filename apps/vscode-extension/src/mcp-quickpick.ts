// Pure MCP entry-assembly helper for the tier-1 QuickPick commands
// (aiEditor.mcpAddServer / aiEditor.mcpListServers, wired in extension.ts).
// vscode-free so it's unit-testable.

export interface McpEntryInput {
  transport: "stdio" | "http" | "sse";
  commandLine?: string;
  url?: string;
  envVarNames: string[];
}

export function buildMcpEntry(input: McpEntryInput): Record<string, unknown> {
  if (input.transport === "stdio") {
    const [command, ...args] = (input.commandLine ?? "").trim().split(/\s+/).filter(Boolean);
    const entry: Record<string, unknown> = { command, args, enabled: true };
    if (input.envVarNames.length) {
      entry.env = Object.fromEntries(input.envVarNames.map((v) => [v, `\${${v}}`]));
    }
    return entry;
  }
  const entry: Record<string, unknown> = {
    type: input.transport,
    url: input.url,
    enabled: true,
  };
  if (input.envVarNames.length) {
    const [first, ...rest] = input.envVarNames;
    const headers: Record<string, string> = { Authorization: `Bearer \${${first}}` };
    for (const name of rest) headers[name] = `\${${name}}`;
    entry.headers = headers;
  }
  return entry;
}
