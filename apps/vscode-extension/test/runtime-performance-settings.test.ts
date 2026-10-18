import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { RuntimeManager } from "../src/runtime/vscode-runtime.js";
import { buildBackendEnv } from "../src/runtime/backend-process.js";

const { getConfiguration } = vi.hoisted(() => ({ getConfiguration: vi.fn() }));
vi.mock("vscode", () => ({ workspace: { getConfiguration } }));

const manifest = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));

describe("retired candidate experiment settings", () => {
  it.each(["candidateTrial", "candidateDelivery", "candidateScorer", "qwenModelPath"])(
    "does not advertise or forward %s", name => {
      const key = `crucible.performance.${name}`;
      expect(manifest.contributes.configuration.properties[key]).toBeUndefined();
      getConfiguration.mockReturnValue({
        inspect: (setting: string) => setting === key ? { workspaceValue: "automatic" } : undefined,
        get: (setting: string, fallback?: unknown) => setting === key ? "automatic" : fallback,
      });
      const mapper = RuntimeManager.prototype as unknown as {
        extraEnvFromSettings(): Record<string, string>;
      };
      expect(Object.keys(mapper.extraEnvFromSettings()).some(
        k => k.startsWith("CRUCIBLE_CODE_CANDIDATE") || k.startsWith("CRUCIBLE_CODE_QWEN"),
      )).toBe(false);
    },
  );
});

describe.each([
  ["crucible.performance.toolBatching", "CRUCIBLE_TOOL_BATCHING_ENABLED"],
  ["crucible.performance.adaptiveReads", "CRUCIBLE_ADAPTIVE_READS_ENABLED"],
  ["crucible.performance.compactContext", "CRUCIBLE_COMPACT_CONTEXT_ENABLED"],
  ["crucible.memory.experimental.recall", "CRUCIBLE_MEMORY_RECALL_V2"],
  ["crucible.memory.experimental.curator", "CRUCIBLE_MEMORY_CURATOR_V2"],
  ["crucible.memory.experimental.compaction", "CRUCIBLE_MEMORY_COMPACT_EVIDENCE"],
])("%s to managed backend", (key, envKey) => {
  it("advertises the enabled default in VS Code settings", () => {
    expect(manifest.contributes.configuration.properties[key].default).toBe(true);
  });

  it.each([undefined, true, false])("enables fresh installs and honors explicit setting %s", (value) => {
    getConfiguration.mockReturnValue({
      inspect: (setting: string) => setting === key ? { globalValue: value } : undefined,
      get: (setting: string, fallback?: unknown) => setting === key ? value : fallback,
    });
    const mapper = RuntimeManager.prototype as unknown as {
      extraEnvFromSettings(): Record<string, string>;
    };
    const extraEnv = mapper.extraEnvFromSettings();
    const env = buildBackendEnv("/ws", { backend: "gemini", model: "m", extraEnv }, "/rt", "darwin-arm64");
    expect(env[envKey]).toBe(value === undefined ? "1" : String(value));
  });
});
