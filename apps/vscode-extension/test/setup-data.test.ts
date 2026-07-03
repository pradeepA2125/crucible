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

  it("PROVIDERS covers all nine, locals have no key var", () => {
    expect(PROVIDERS.map((p) => p.id).sort()).toEqual([
      "anthropic", "gemini", "groq", "huggingface", "ollama",
      "openai", "openrouter", "turboquant", "watsonx"]);
    expect(PROVIDERS.find((p) => p.id === "ollama")!.keyEnvVar).toBeUndefined();
    expect(PROVIDERS.every((p) => p.defaultModel.length > 0)).toBe(true);
  });
});
