import { describe, expect, it } from "vitest";
import { buildMcpEntry } from "../src/mcp-quickpick.js";

describe("buildMcpEntry", () => {
  it("stdio: splits command line, env vars become ${VAR} refs, enabled true", () => {
    expect(buildMcpEntry({
      transport: "stdio", commandLine: "uv run server.py --x",
      envVarNames: ["API_KEY"] })).toEqual({
      command: "uv", args: ["run", "server.py", "--x"],
      env: { API_KEY: "${API_KEY}" }, enabled: true });
  });
  it("http: url + headers from env var names", () => {
    expect(buildMcpEntry({
      transport: "http", url: "https://x", envVarNames: ["GITHUB_PAT"] })).toEqual({
      type: "http", url: "https://x",
      headers: { Authorization: "Bearer ${GITHUB_PAT}" }, enabled: true });
  });
  it("no env vars: omits env/headers", () => {
    expect(buildMcpEntry({ transport: "sse", url: "https://y", envVarNames: [] }))
      .toEqual({ type: "sse", url: "https://y", enabled: true });
  });
});
