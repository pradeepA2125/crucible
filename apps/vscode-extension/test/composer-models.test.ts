import { describe, expect, it } from "vitest";
import { buildEffortRows, buildModelOptions } from "../src/composer-models.js";
import { PROVIDERS } from "../src/setup-data.js";

describe("buildModelOptions", () => {
  it("offers only keyed providers, marking the current one active with its live model", () => {
    const options = buildModelOptions(
      { backend: "gemini", model: "gemini-flash-latest" },
      ["gemini", "anthropic"],
      PROVIDERS,
    );
    const ids = options.map((o) => o.backend);
    expect(ids).toContain("gemini");
    expect(ids).toContain("anthropic");
    expect(ids).not.toContain("openai");
    const gemini = options.find((o) => o.backend === "gemini")!;
    expect(gemini).toMatchObject({ model: "gemini-flash-latest", active: true });
    const anthropic = options.find((o) => o.backend === "anthropic")!;
    expect(anthropic.active).toBe(false);
    expect(anthropic.model).toBe(PROVIDERS.find((p) => p.id === "anthropic")!.defaultModel);
  });

  it("includes an unkeyed local provider only when it is current", () => {
    const withCurrent = buildModelOptions({ backend: "ollama", model: "qwen3:8b" }, [], PROVIDERS);
    expect(withCurrent).toEqual([{ backend: "ollama", label: "Ollama (local)", model: "qwen3:8b", active: true }]);
    const without = buildModelOptions(null, [], PROVIDERS);
    expect(without).toEqual([]);
  });
});

describe("buildModelOptions with a signed-in ChatGPT account", () => {
  const catalog = [{ slug: "gpt-a", displayName: "GPT A" }, { slug: "gpt-b", displayName: "GPT B" }];

  it("offers every catalog model, by display name, marking the one in use", () => {
    const options = buildModelOptions({ backend: "chatgpt", model: "gpt-b" }, ["gemini"],
                                      PROVIDERS, catalog);
    expect(options.filter((o) => o.backend === "chatgpt")).toEqual([
      { backend: "chatgpt", label: "ChatGPT plan", model: "gpt-a", display: "GPT A", active: false },
      { backend: "chatgpt", label: "ChatGPT plan", model: "gpt-b", display: "GPT B", active: true },
    ]);
    expect(options.some((o) => o.backend === "gemini")).toBe(true);
  });

  it("offers the plan's models even when another provider is current", () => {
    const options = buildModelOptions({ backend: "gemini", model: "g" }, ["gemini"], PROVIDERS,
                                      catalog);
    expect(options.filter((o) => o.backend === "chatgpt").every((o) => !o.active)).toBe(true);
    expect(options.filter((o) => o.backend === "chatgpt")).toHaveLength(2);
  });

  it("without a catalog the plan offers only the model in use", () => {
    const options = buildModelOptions({ backend: "chatgpt", model: "gpt-b" }, [], PROVIDERS);
    expect(options).toEqual([
      { backend: "chatgpt", label: "ChatGPT plan", model: "gpt-b", active: true }]);
  });
});

describe("buildEffortRows", () => {
  it("marks each rung supported, unsupported, or unknown", () => {
    const rows = buildEffortRows({
      supported: ["off", "low", "high"],
      unsupported: { max: "tops out at high" },
    });
    expect(rows.map((r) => r.level)).toEqual(["off", "low", "medium", "high", "max"]);
    expect(rows.find((r) => r.level === "low")?.state).toBe("supported");
    expect(rows.find((r) => r.level === "max")?.state).toBe("unsupported");
    expect(rows.find((r) => r.level === "max")?.reason).toBe("tops out at high");
    // Not listed either way: unverified, still selectable.
    expect(rows.find((r) => r.level === "medium")?.state).toBe("unknown");
  });

  it("treats a null support map as entirely unknown rather than unsupported", () => {
    const rows = buildEffortRows(null);
    expect(rows.every((r) => r.state === "unknown")).toBe(true);
  });
});
