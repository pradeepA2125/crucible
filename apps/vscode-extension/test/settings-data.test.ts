import { describe, expect, it, vi } from "vitest";
import {
  createSettingsHandler,
  type SettingsDeps,
  type SettingsOutMsg,
} from "../src/settings-data.js";

function stateMsg(
  msg: SettingsOutMsg,
): Extract<SettingsOutMsg, { type: "settings/state" }> {
  if (msg.type !== "settings/state") {
    throw new Error(`expected settings/state, got ${msg.type}`);
  }
  return msg;
}

function deps(overrides: Partial<SettingsDeps> = {}): SettingsDeps & {
  disabled: string[];
  skillsBox: string[];
} {
  const box = { disabled: [] as string[], skills: [] as string[] };
  return {
    client: {
      getConfig: async () => ({ provider: { backend: "openai", model: "gpt-5" } }),
      listMcpServers: async () => ({
        enabled: true,
        servers: [
          {
            name: "web",
            transport: "stdio",
            enabledInFile: true,
            state: "connected",
            detail: null,
            toolCount: 2,
          },
        ],
      }),
      listSkills: async () => [{ name: "s1", description: "d" }],
      validateProvider: async () => ({ ok: true }),
      setProvider: async () => ({ backend: "groq", model: "m2" }),
      upsertMcpServer: async () => ({ enabled: true, servers: [] }),
      deleteMcpServer: async () => ({ enabled: true, servers: [] }),
      reconnectMcpServer: vi.fn(async () => ({ enabled: true, servers: [] })),
    },
    workspace: "/ws",
    readRuntimeJson: () => ({ releaseTag: "v0.1.0", components: {} }),
    mcpDisabled: () => box.disabled,
    setMcpDisabled: async (n) => {
      box.disabled = n;
    },
    skillsDisabled: () => box.skills,
    setSkillsDisabled: async (n) => {
      box.skills = n;
    },
    storeSecret: async () => {},
    keyEnvVar: () => "X_KEY",
    readEnvFlags: () => ({ "aiEditor.policy.shell": "ask" }),
    updateSetting: async () => {},
    readInstructions: () => ({ content: "", exists: false }),
    writeInstructions: () => {},
    restartBackend: async () => {},
    disabled: box.disabled,
    skillsBox: box.skills,
    ...overrides,
  };
}

describe("createSettingsHandler", () => {
  it("load posts a full state snapshot", async () => {
    const posted: SettingsOutMsg[] = [];
    await createSettingsHandler(deps(), (m) => posted.push(m))({ type: "settings/load" });
    const state = stateMsg(posted[0]).state;
    expect(state.provider).toEqual({ backend: "openai", model: "gpt-5" });
    expect(state.mcp.servers[0].userEnabled).toBe(true);
    expect(state.skills).toEqual([{ name: "s1", description: "d", enabled: true }]);
  });

  it("setProvider validates first and aborts on failure", async () => {
    const posted: SettingsOutMsg[] = [];
    const setProvider = vi.fn();
    const d = deps();
    d.client.validateProvider = async () => ({ ok: false, error: "bad key" });
    d.client.setProvider = setProvider;
    await createSettingsHandler(d, (m) => posted.push(m))({
      type: "settings/setProvider",
      backend: "groq",
      model: "m",
      apiKey: "k",
    });
    expect(setProvider).not.toHaveBeenCalled();
    expect(posted).toEqual([{ type: "settings/error", message: "bad key" }]);
  });

  it("mcpToggle updates user-local disabled list and reconnects with it", async () => {
    const d = deps();
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({ type: "settings/mcpToggle", name: "web", enabled: false });
    expect(d.mcpDisabled()).toEqual(["web"]);
    expect(d.client.reconnectMcpServer).toHaveBeenCalledWith("web", ["web"]);
  });

  it("skillToggle flags restartRequired", async () => {
    const posted: SettingsOutMsg[] = [];
    await createSettingsHandler(deps(), (m) => posted.push(m))({
      type: "settings/skillToggle",
      name: "s1",
      enabled: false,
    });
    const state = stateMsg(posted.find((m) => m.type === "settings/state")!).state;
    expect(state.restartRequired).toBe(true);
    expect(state.skills[0].enabled).toBe(false);
  });

  it("restartBackend clears restartRequired after restarting", async () => {
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(deps(), (m) => posted.push(m));
    await handle({ type: "settings/skillToggle", name: "s1", enabled: false });
    await handle({ type: "settings/restartBackend" });
    const last = stateMsg(posted[posted.length - 1]).state;
    expect(last.restartRequired).toBe(false);
  });

  it("loadInstructions posts the file state", async () => {
    const posted: SettingsOutMsg[] = [];
    const d = deps({ readInstructions: () => ({ content: "# hi", exists: true }) });
    await createSettingsHandler(d, (m) => posted.push(m))({ type: "settings/loadInstructions" });
    expect(posted).toContainEqual({ type: "settings/instructions", content: "# hi", exists: true });
  });

  it("saveInstructions writes then echoes the saved state", async () => {
    const written: string[] = [];
    const posted: SettingsOutMsg[] = [];
    const d = deps({ writeInstructions: (c: string) => written.push(c) });
    await createSettingsHandler(d, (m) => posted.push(m))({
      type: "settings/saveInstructions",
      content: "# new",
    });
    expect(written).toEqual(["# new"]);
    expect(posted).toContainEqual({ type: "settings/instructions", content: "# new", exists: true });
  });
});
