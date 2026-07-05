import { describe, it, expect } from "vitest";
import { buildMcpEntry, splitCommandLine } from "./mcpEntry";

describe("splitCommandLine", () => {
  it("honors quoted segments containing spaces", () => {
    expect(splitCommandLine('uv run "/Users/x/AI editor/server.py"')).toEqual([
      "uv", "run", "/Users/x/AI editor/server.py",
    ]);
  });
});

describe("buildMcpEntry", () => {
  it("stdio: command/args + ${VAR} env refs", () => {
    expect(
      buildMcpEntry({ transport: "stdio", commandLine: "uv run s.py", url: "", envVarNames: ["OLLAMA_API_KEY"] }),
    ).toEqual({
      command: "uv", args: ["run", "s.py"], enabled: true,
      env: { OLLAMA_API_KEY: "${OLLAMA_API_KEY}" },
    });
  });

  it("http: first env var becomes a Bearer Authorization header", () => {
    expect(
      buildMcpEntry({ transport: "http", commandLine: "", url: "https://x", envVarNames: ["GITHUB_PAT"] }),
    ).toEqual({
      type: "http", url: "https://x", enabled: true,
      headers: { Authorization: "Bearer ${GITHUB_PAT}" },
    });
  });
});
