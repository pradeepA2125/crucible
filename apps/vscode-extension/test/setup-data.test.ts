import { describe, expect, it } from "vitest";
import { createSetupHandler, PROVIDERS, type SetupDeps } from "../src/setup-data.js";

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
    ...overrides,
  };
}

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

  it("PROVIDERS covers all ten, locals have no key var", () => {
    expect(PROVIDERS.map((p) => p.id).sort()).toEqual([
      "anthropic", "gemini", "groq", "huggingface", "ollama", "openai",
      "openai_compatible", "openrouter", "turboquant", "watsonx"]);
    expect(PROVIDERS.find((p) => p.id === "ollama")!.keyEnvVar).toBeUndefined();
    // openai_compatible is the one deliberate exception: it has no default model
    // (the user must supply one — see the "openai_compatible provider entry" suite).
    expect(PROVIDERS.every((p) => p.id === "openai_compatible" || p.defaultModel.length > 0)).toBe(true);
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
    await handle({ type: "setup/save", backend: "openai_compatible", model: "m" });
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
