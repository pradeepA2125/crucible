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

const CATALOG = {
  agents: [{
    name: "helper", description: "d", tools: null, disallowedTools: [], permission: "default",
    declaredPermission: "default", model: "inherit", maxTurns: null, skills: [],
    source: "crucible" as const, path: "/ws/.crucible/agents/helper.md", sha256: "ab",
    trust: "trusted" as const, active: true, warnings: [], shadowedBy: null, persona: "p",
    content: "c",
  }],
  skipped: [],
  availableTools: ["read_file", "edit"],
};

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
      testContextWindow: async () => ({ ok: true, recalled: true }),
      listAgentDefinitions: vi.fn(async () => CATALOG),
      saveAgentDefinition: vi.fn(async () => CATALOG.agents[0]!),
      deleteAgentDefinition: vi.fn(async () => {}),
      trustAgentDefinition: vi.fn(async () => {}),
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
    deleteSecret: async () => {},
    storeExtraCredentials: async () => {},
    keyEnvVar: () => "X_KEY",
    readEnvFlags: () => ({ "crucible.policy.shell": "ask" }),
    updateSetting: async () => {},
    readInstructions: () => ({ content: "", exists: false }),
    writeInstructions: () => {},
    restartBackend: async () => {},
    saveContextWindow: async () => {},
    openFile: vi.fn(async () => {}),
    listModels: async () => ["gpt-5", "m2"],
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
    // The state refresh must land BEFORE the error: SettingsApp clears its error
    // banner on every settings/state, so the reverse order would erase the message.
    expect(posted.map((m) => m.type)).toEqual(["settings/state", "settings/error"]);
    expect(posted[posted.length - 1]).toEqual({ type: "settings/error", message: "bad key" });
  });

  it("setProvider carries a validate warning into subsequent state snapshots", async () => {
    const posted: SettingsOutMsg[] = [];
    const d = deps();
    d.client.validateProvider = async () => ({ ok: true, model: "m", warning: "degraded json mode" });
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({ type: "settings/setProvider", backend: "openai_compatible", model: "m" });
    const state = stateMsg(posted[posted.length - 1]).state;
    expect(state.providerWarning).toBe("degraded json mode");
  });

  it("a later failed validate clears a stale providerWarning from an earlier successful one", async () => {
    const posted: SettingsOutMsg[] = [];
    const d = deps();
    const handle = createSettingsHandler(d, (m) => posted.push(m));

    // First attempt: openai_compatible validates ok but with a degraded-json-mode warning.
    d.client.validateProvider = async () => ({ ok: true, model: "m", warning: "degraded json mode" });
    await handle({ type: "settings/setProvider", backend: "openai_compatible", model: "m" });

    // Second attempt: a different provider (or the same one with a bad Base URL) fails
    // validation entirely. The stale warning from the first attempt must not survive —
    // it describes a validate result that's no longer in effect.
    posted.length = 0;
    d.client.validateProvider = async () => ({ ok: false, error: "bad key" });
    await handle({ type: "settings/setProvider", backend: "openai", model: "gpt-5", apiKey: "bad" });

    // The failure path itself must push the cleared state — the webview never sends a
    // follow-up `settings/load` in this flow, so a handler that only posts
    // `settings/error` leaves the stale amber warning rendered next to the red error.
    const refreshed = posted.filter((m) => m.type === "settings/state");
    expect(refreshed).toHaveLength(1);
    expect(stateMsg(refreshed[0]).state.providerWarning).toBeFalsy();
  });

  it("clearProviderKey deletes the selected backend's secret and flags a restart", async () => {
    const posted: SettingsOutMsg[] = [];
    const deleted: string[] = [];
    const d = deps({ deleteSecret: async (backend: string) => void deleted.push(backend) });
    await createSettingsHandler(d, (m) => posted.push(m))({
      type: "settings/clearProviderKey",
      backend: "openai_compatible",
    });
    // Scoped to the backend named in the message — never the active saved provider
    // (deps().getConfig reports "openai"), so switching the dropdown without saving
    // and clearing removes the key the user is actually looking at.
    expect(deleted).toEqual(["openai_compatible"]);
    // The running managed backend still has the old key in its spawn env.
    expect(stateMsg(posted[posted.length - 1]).state.restartRequired).toBe(true);
  });

  it("clearProviderKey surfaces a delete failure instead of failing silently", async () => {
    const posted: SettingsOutMsg[] = [];
    const d = deps({
      deleteSecret: async () => {
        throw new Error("secret storage unavailable");
      },
    });
    await createSettingsHandler(d, (m) => posted.push(m))({
      type: "settings/clearProviderKey",
      backend: "openai_compatible",
    });
    expect(posted).toEqual([
      { type: "settings/error", message: "secret storage unavailable" },
    ]);
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

describe("context window", () => {
  it("forwards contextWindow to setProvider and persists it", async () => {
    const setProvider = vi.fn(async () => ({ backend: "groq", model: "m2" }));
    const saveContextWindow = vi.fn(async () => {});
    const d = deps({ saveContextWindow });
    d.client.setProvider = setProvider;
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({
      type: "settings/setProvider", backend: "groq", model: "m2", contextWindow: 32768,
    });
    expect(setProvider).toHaveBeenCalledWith(
      expect.objectContaining({ contextWindow: 32768 }),
    );
    expect(saveContextWindow).toHaveBeenCalledWith(32768);
  });

  it("does not persist the window when validation fails", async () => {
    const saveContextWindow = vi.fn(async () => {});
    const d = deps({ saveContextWindow });
    d.client.validateProvider = async () => ({ ok: false, error: "bad key" });
    const handle = createSettingsHandler(d, () => {});
    await handle({
      type: "settings/setProvider", backend: "groq", model: "m2", contextWindow: 32768,
    });
    expect(saveContextWindow).not.toHaveBeenCalled();
  });

  it("does not persist the window when setProvider throws after a successful validate", async () => {
    // The ordering guarantee (save happens strictly after the hot-swap succeeds)
    // today holds only by statement order inside the case body. This pins it: a
    // later "eager save" that moves saveContextWindow above the setProvider call
    // would fail this test.
    const saveContextWindow = vi.fn(async () => {});
    const d = deps({ saveContextWindow });
    d.client.setProvider = async () => {
      throw new Error("hot-swap rejected");
    };
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({
      type: "settings/setProvider", backend: "groq", model: "m2", contextWindow: 32768,
    });
    expect(saveContextWindow).not.toHaveBeenCalled();
    expect(posted).toEqual([{ type: "settings/error", message: "hot-swap rejected" }]);
  });

  it("posts the context-test verdict on its own message, without a settings/state rebuild", async () => {
    const d = deps();
    d.client.testContextWindow = async () => ({
      ok: true, recalled: false, promptTokens: 141234, exact: true,
    });
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({
      type: "settings/testContextWindow", backend: "groq", model: "m2", contextWindow: 128000,
    });
    const verdicts = posted.filter((m) => m.type === "settings/contextTestResult");
    expect(verdicts).toHaveLength(1);
    expect(verdicts[0]).toMatchObject({ result: { recalled: false, promptTokens: 141234 } });
    // Pins the deliberate skip of postState(): a verdict is not a change to the
    // settings snapshot, so an accidental postState() added to this case must fail here.
    expect(posted.some((m) => m.type === "settings/state")).toBe(false);
  });

  it("reports a failed test on the verdict message, not the error banner", async () => {
    /* The route returns 200 with ok:false — that belongs next to the field, not in
       the panel-wide red banner reserved for things that actually broke. */
    const d = deps();
    d.client.testContextWindow = async () => ({ ok: false, recalled: false, error: "429 rate limited" });
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({
      type: "settings/testContextWindow", backend: "groq", model: "m2", contextWindow: 128000,
    });
    expect(posted.some((m) => m.type === "settings/error")).toBe(false);
    expect(posted.find((m) => m.type === "settings/contextTestResult")).toMatchObject({
      result: { ok: false, error: "429 rate limited" },
    });
  });

  it("a thrown testContextWindow still reaches the catch-all as settings/error", async () => {
    // The other direction of the ok:false split: a genuine exception (backend
    // unreachable) must NOT be silently swallowed into a contextTestResult verdict.
    const d = deps();
    d.client.testContextWindow = async () => {
      throw new Error("backend unreachable");
    };
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({
      type: "settings/testContextWindow", backend: "groq", model: "m2", contextWindow: 128000,
    });
    expect(posted).toEqual([{ type: "settings/error", message: "backend unreachable" }]);
  });
});

describe("agents messages", () => {
  const run = async (d: ReturnType<typeof deps>, msg: Parameters<ReturnType<typeof createSettingsHandler>>[0]) => {
    const posted: SettingsOutMsg[] = [];
    await createSettingsHandler(d, (m) => posted.push(m))(msg);
    return posted;
  };

  it("listAgents posts the catalog, not a settings snapshot", async () => {
    const posted = await run(deps(), { type: "settings/listAgents" });
    expect(posted).toEqual([{ type: "settings/agents", catalog: CATALOG }]);
  });

  it("a list failure stays inside the section", async () => {
    const d = deps();
    d.client.listAgentDefinitions = async () => { throw new Error("backend down"); };
    const posted = await run(d, { type: "settings/listAgents" });
    expect(posted).toEqual([{ type: "settings/agentsError", message: "backend down" }]);
  });

  it("save and delete re-post the list", async () => {
    const d = deps();
    const input = { description: "d", persona: "p", tools: null, disallowedTools: [],
      permission: "default", model: "inherit", maxTurns: null, skills: [] };
    expect((await run(d, { type: "settings/saveAgent", name: "helper", input })).at(-1))
      .toEqual({ type: "settings/agents", catalog: CATALOG });
    expect(d.client.saveAgentDefinition).toHaveBeenCalledWith("helper", input);
    expect((await run(d, { type: "settings/deleteAgent", name: "helper" })).at(-1)?.type)
      .toBe("settings/agents");
  });

  it("trustAgent conflict refreshes the list after the error", async () => {
    const d = deps();
    d.client.trustAgentDefinition = async () => { throw new Error("the file changed since you reviewed it"); };
    const posted = await run(d, { type: "settings/trustAgent", path: "/p.md", sha256: "ab" });
    expect(posted.map((m) => m.type)).toEqual(["settings/agentsError", "settings/agents"]);
    expect(posted[0]).toEqual({ type: "settings/agentsError",
      message: "the file changed since you reviewed it" });
  });

  it("openFile and listModels", async () => {
    const d = deps();
    expect(await run(d, { type: "settings/openFile", path: "/a.md" })).toEqual([]);
    expect(d.openFile).toHaveBeenCalledWith("/a.md");
    expect(await run(d, { type: "settings/listModels" }))
      .toEqual([{ type: "settings/models", models: ["gpt-5", "m2"] }]);
  });
});
