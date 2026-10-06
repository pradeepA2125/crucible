import { describe, expect, it, vi } from "vitest";
import {
  createSetupHandler,
  missingRequiredFields,
  PROVIDERS,
  type SetupDeps,
} from "../src/setup-data.js";

function deps(overrides: Partial<SetupDeps> = {}): SetupDeps {
  return {
    install: async (onProgress) => {
      onProgress({ id: "uv", status: "done" });
      return { ok: true };
    },
    validate: async () => ({ ok: true, model: "m" }),
    saveAndStart: async () => ({ port: 8123 }),
    openChat: () => {},
    keyEnvVar: (b) => (b === "ollama" ? undefined : "X_KEY"),
    storedExtraEnvVars: () => [],
    startForChatGPT: async () => ({ port: 8124 }),
    chatgpt: {
      client: {
        startChatGPTSignIn: vi.fn(),
        getChatGPTSignIn: vi.fn(),
        cancelChatGPTSignIn: vi.fn(),
        listChatGPTAccounts: vi.fn(async () => []),
        signOutChatGPT: vi.fn(),
        listChatGPTModels: vi.fn(),
        validateProvider: vi.fn(async () => ({ ok: true })),
        setProvider: vi.fn(async () => ({ backend: "chatgpt", model: "m1" })),
      },
      openExternal: vi.fn(async () => {}),
      activeRegistration: () => undefined,
      saveActiveRegistration: vi.fn(async () => {}),
      saveContextWindow: vi.fn(async () => {}),
      welcomedRegistrations: () => [],
      markWelcomed: vi.fn(async () => {}),
      sleep: async () => {},
      now: () => 0,
    },
    ...overrides,
  };
}

describe("setup with the ChatGPT plan", () => {
  it("starts the backend for sign-in, then finishes setup when an account is put to use", async () => {
    const posted: unknown[] = [];
    const handle = createSetupHandler(deps(), (m) => posted.push(m));
    await handle({ type: "setup/startForChatGPT" });
    expect(posted).toEqual([{ type: "setup/chatgptReady", hasAccounts: false }]);
    await handle({ type: "settings/useChatGPT", registrationId: "reg_aaaaaaaaaaaa", model: "m1" });
    expect(posted).toContainEqual({ type: "setup/ready", port: 8124 });
  });

  it("reports saved accounts so a returning user reuses one instead of registering anew", async () => {
    const posted: unknown[] = [];
    const d = deps();
    d.chatgpt.client.listChatGPTAccounts = vi.fn(async () => [{
      registrationId: "reg_aaaaaaaaaaaa", label: "a@x.com", email: "a@x.com", name: null,
      planEnabled: true, signedIn: true }]);
    await createSetupHandler(d, (m) => posted.push(m))({ type: "setup/startForChatGPT" });
    expect(posted).toEqual([{ type: "setup/chatgptReady", hasAccounts: true }]);
  });
});

describe("createSetupHandler", () => {
  it("install relays progress then installDone", async () => {
    const posted: unknown[] = [];
    const handle = createSetupHandler(deps(), (m) => posted.push(m));
    await handle({ type: "setup/install" });
    expect(posted).toEqual([
      { type: "setup/progress", component: "uv", status: "done", detail: undefined },
      { type: "setup/installDone", ok: true },
    ]);
  });

  it("validate maps apiKey to the provider env var", async () => {
    const posted: unknown[] = [];
    let seen: Record<string, string> | undefined;
    const handle = createSetupHandler(deps({
      validate: async (req) => { seen = req.credentials; return { ok: false, error: "bad" }; },
    }), (m) => posted.push(m));
    await handle({ type: "setup/validate", backend: "groq", model: "m", apiKey: "k" });
    expect(seen).toEqual({ X_KEY: "k" });
    expect(posted).toEqual([{ type: "setup/validateResult", ok: false, error: "bad" }]);
  });

  it("save starts the backend and posts ready; errors become setup/error", async () => {
    const posted: unknown[] = [];
    const handle = createSetupHandler(deps({
      saveAndStart: async () => { throw new Error("spawn failed"); },
    }), (m) => posted.push(m));
    await handle({ type: "setup/save", backend: "groq", model: "m", apiKey: "k" });
    expect(posted).toEqual([{ type: "setup/error", message: "spawn failed" }]);
  });

  it("save posts ready on success", async () => {
    const posted: unknown[] = [];
    const handle = createSetupHandler(deps(), (m) => posted.push(m));
    await handle({ type: "setup/save", backend: "groq", model: "m" });
    expect(posted).toEqual([{ type: "setup/ready", port: 8123 }]);
  });

  it("PROVIDERS covers all eleven, locals have no key var", () => {
    expect(PROVIDERS.map((p) => p.id).sort()).toEqual([
      "anthropic", "chatgpt", "gemini", "groq", "huggingface", "ollama", "openai",
      "openai_compatible", "openrouter", "turboquant", "watsonx"]);
    expect(PROVIDERS.find((p) => p.id === "ollama")!.keyEnvVar).toBeUndefined();
    // Two deliberate exceptions have no default model: openai_compatible (the user
    // supplies one) and chatgpt (models come from the signed-in account's catalog).
    expect(PROVIDERS.every((p) => ["openai_compatible", "chatgpt"].includes(p.id)
      || p.defaultModel.length > 0)).toBe(true);
    const chatgpt = PROVIDERS.find((p) => p.id === "chatgpt")!;
    expect(chatgpt.signIn).toBe("chatgpt");
    expect(chatgpt.keyEnvVar).toBeUndefined();
  });

  it("validate relays jsonMode/warning through to setup/validateResult when present", async () => {
    const posted: unknown[] = [];
    const handle = createSetupHandler(deps({
      validate: async () => ({ ok: true, model: "m", jsonMode: "json_object", warning: "degraded" }),
    }), (m) => posted.push(m));
    await handle({ type: "setup/validate", backend: "openai_compatible", model: "m" });
    expect(posted).toEqual([{
      type: "setup/validateResult", ok: true, model: "m", jsonMode: "json_object", warning: "degraded",
    }]);
  });

  it("save relays jsonMode/warning through to setup/ready when present", async () => {
    const posted: unknown[] = [];
    const handle = createSetupHandler(deps({
      saveAndStart: async () => ({ port: 8123, jsonMode: "json_object", warning: "degraded" }),
    }), (m) => posted.push(m));
    // Base URL supplied deliberately: without it this save is exactly the
    // incomplete config that spawns a backend which dies at import, and the
    // guard now (correctly) refuses it.
    await handle({
      type: "setup/save", backend: "openai_compatible", model: "m",
      extraCredentials: { CRUCIBLE_OPENAI_COMPAT_BASE_URL: "https://x/v1" },
    });
    expect(posted).toEqual([{
      type: "setup/ready", port: 8123, jsonMode: "json_object", warning: "degraded",
    }]);
  });
});

describe("openai_compatible provider entry", () => {
  const provider = PROVIDERS.find((p) => p.id === "openai_compatible");

  it("is registered", () => {
    expect(provider).toBeDefined();
  });

  it("has an optional key so keyless local endpoints can be saved", () => {
    // vLLM / LM Studio have no API key; the wizard must not block save.
    expect(provider!.local).toBe(false);
    expect(provider!.keyOptional).toBe(true);
  });

  it("requires a base URL field with a usable placeholder", () => {
    const field = provider!.extraFields?.find(
      (f) => f.envVar === "CRUCIBLE_OPENAI_COMPAT_BASE_URL",
    );
    expect(field).toBeDefined();
    expect(field!.optional).toBeFalsy();
    expect(field!.placeholder).toContain("http");
  });

  it("has no default model so the user must supply one", () => {
    expect(provider!.defaultModel).toBe("");
  });
});

describe("missingRequiredFields", () => {
  it("accepts a fully-specified provider", () => {
    expect(missingRequiredFields("groq", "m", undefined, [])).toEqual([]);
  });

  it("flags a required extra field the user left blank", () => {
    /* This is the wizard's real footgun: openai_compatible without a Base URL
       persists, spawns a backend, and agentd dies at import with
       "CRUCIBLE_OPENAI_COMPAT_BASE_URL is required" — a raw traceback where a
       field-level message belongs. */
    expect(missingRequiredFields("openai_compatible", "m", {}, []))
      .toEqual(["Base URL"]);
  });

  it("treats whitespace as blank", () => {
    expect(missingRequiredFields("openai_compatible", "m",
      { CRUCIBLE_OPENAI_COMPAT_BASE_URL: "   " }, [])).toEqual(["Base URL"]);
  });

  it("accepts a supplied extra field", () => {
    expect(missingRequiredFields("openai_compatible", "m",
      { CRUCIBLE_OPENAI_COMPAT_BASE_URL: "https://x/v1" }, [])).toEqual([]);
  });

  it("accepts a blank field that is already stored from a previous run", () => {
    /* Blank means "keep the stored value" for a returning user — blocking that
       would be a false alarm, not a guard. */
    expect(missingRequiredFields("openai_compatible", "m", {},
      ["CRUCIBLE_OPENAI_COMPAT_BASE_URL"])).toEqual([]);
  });

  it("flags a blank model, which openai_compatible ships by default", () => {
    const entry = PROVIDERS.find((p) => p.id === "openai_compatible")!;
    expect(entry.defaultModel).toBe("");
    expect(missingRequiredFields("openai_compatible", "  ",
      { CRUCIBLE_OPENAI_COMPAT_BASE_URL: "https://x/v1" }, [])).toEqual(["Model"]);
  });

  it("reports every missing field at once, not just the first", () => {
    expect(missingRequiredFields("watsonx", "", {}, []))
      .toEqual(["Model", "Space ID", "URL"]);
  });

  it("says nothing about an unknown provider it cannot describe", () => {
    expect(missingRequiredFields("nope", "m", {}, [])).toEqual([]);
  });
});

describe("setup/save guard", () => {
  it("refuses to spawn a backend that cannot construct, naming the field", async () => {
    const posted: unknown[] = [];
    let started = false;
    const handle = createSetupHandler(deps({
      saveAndStart: async () => { started = true; return { port: 1 }; },
    }), (m) => posted.push(m));
    await handle({ type: "setup/save", backend: "openai_compatible", model: "m", extraCredentials: {} });
    expect(started).toBe(false);
    expect(posted).toHaveLength(1);
    expect(posted[0]).toMatchObject({ type: "setup/error" });
    expect((posted[0] as { message: string }).message).toContain("Base URL");
  });

  it("still saves when the blank field is already stored", async () => {
    let started = false;
    const handle = createSetupHandler(deps({
      storedExtraEnvVars: () => ["CRUCIBLE_OPENAI_COMPAT_BASE_URL"],
      saveAndStart: async () => { started = true; return { port: 8123 }; },
    }), () => {});
    await handle({ type: "setup/save", backend: "openai_compatible", model: "m", extraCredentials: {} });
    expect(started).toBe(true);
  });

  it("leaves a complete provider untouched", async () => {
    let started = false;
    const handle = createSetupHandler(deps({
      saveAndStart: async () => { started = true; return { port: 8123 }; },
    }), () => {});
    await handle({ type: "setup/save", backend: "groq", model: "m", apiKey: "k" });
    expect(started).toBe(true);
  });
});
