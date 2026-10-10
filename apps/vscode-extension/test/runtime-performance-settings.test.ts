import { describe, expect, it, vi } from "vitest";
import { RuntimeManager } from "../src/runtime/vscode-runtime.js";
import { buildBackendEnv } from "../src/runtime/backend-process.js";

const { getConfiguration } = vi.hoisted(() => ({ getConfiguration: vi.fn() }));
vi.mock("vscode", () => ({ workspace: { getConfiguration } }));

describe.each([
  ["crucible.performance.toolBatching", "CRUCIBLE_TOOL_BATCHING_ENABLED"],
  ["crucible.performance.adaptiveReads", "CRUCIBLE_ADAPTIVE_READS_ENABLED"],
  ["crucible.performance.compactContext", "CRUCIBLE_COMPACT_CONTEXT_ENABLED"],
])("%s to managed backend", (key, envKey) => {
  it.each([undefined, true, false])("preserves opt-in setting %s", (value) => {
    getConfiguration.mockReturnValue({
      inspect: (setting: string) => setting === key ? { globalValue: value } : undefined,
      get: (setting: string) => setting === key ? value : undefined,
    });
    const mapper = RuntimeManager.prototype as unknown as {
      extraEnvFromSettings(): Record<string, string>;
    };
    const extraEnv = mapper.extraEnvFromSettings();
    const env = buildBackendEnv("/ws", { backend: "gemini", model: "m", extraEnv }, "/rt", "darwin-arm64");
    expect(env[envKey]).toBe(value === undefined ? undefined : String(value));
  });
});
