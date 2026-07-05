import { describe, expect, it } from "vitest";
import { buildModelOptions } from "../src/composer-models.js";
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
