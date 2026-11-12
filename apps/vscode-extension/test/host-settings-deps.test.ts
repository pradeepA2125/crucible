import { describe, expect, it, vi } from "vitest";
import { createHostSettingsDeps, type HostSettingsOptions } from "../src/host-settings-deps.js";

function wiring() {
  const client = {
    getProjectTrust: vi.fn(async () => ({ files: [{ kind: "mcp", state: "untrusted", sha256: "a".repeat(64) }] })),
    trustProjectFile: vi.fn(async () => {}),
    setMcpSecrets: vi.fn(async () => ({ enabled: true, trusted: true, servers: [] })),
  };
  const runtimeManager = { storeMcpSecrets: vi.fn(async () => {}) };
  const deps = createHostSettingsDeps({
    runtimeManager: runtimeManager as unknown as HostSettingsOptions["runtimeManager"],
    native: {} as HostSettingsOptions["native"],
    openExternal: async () => {},
    workspacePath: "/ws/project",
    clientFactory: (() => client) as unknown as HostSettingsOptions["clientFactory"],
    resolveBackendUrl: () => "http://127.0.0.1:1",
    setSandboxEnabled: async () => {},
  });
  return { deps, client, runtimeManager };
}

describe("shared Settings wiring", () => {
  it("reads and sets repository-file trust, so Settings can review and trust mcp.json", async () => {
    const { deps, client } = wiring();
    expect((await deps.client.getProjectTrust!()).files[0]).toMatchObject({ kind: "mcp", state: "untrusted" });
    await deps.client.trustProjectFile!("mcp", "a".repeat(64));
    expect(client.trustProjectFile).toHaveBeenCalledWith("mcp", "a".repeat(64));
  });

  it("stores MCP secrets for this project and sends them to the backend", async () => {
    const { deps, client, runtimeManager } = wiring();
    await deps.storeMcpSecrets({ GITHUB_PAT: "tok" });
    expect(runtimeManager.storeMcpSecrets).toHaveBeenCalledWith("/ws/project", { GITHUB_PAT: "tok" });
    await deps.client.setMcpSecrets({ GITHUB_PAT: "tok" }, ["off"]);
    expect(client.setMcpSecrets).toHaveBeenCalledWith({ GITHUB_PAT: "tok" }, ["off"]);
  });
});
