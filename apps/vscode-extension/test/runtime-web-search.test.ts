import { describe, expect, it, vi } from "vitest";
import { RuntimeManager } from "../src/runtime/vscode-runtime.js";
import { buildBackendEnv } from "../src/runtime/backend-process.js";

const { getConfiguration } = vi.hoisted(() => ({ getConfiguration: vi.fn() }));
vi.mock("vscode", () => ({ workspace: { getConfiguration } }));

describe.each([
  ["crucible.webSearch.enabled", "CRUCIBLE_WEB_SEARCH_ENABLED", "1"],
  ["crucible.webFetch.enabled", "CRUCIBLE_WEB_FETCH_ENABLED", "1"],
])("%s to managed env", (key, envKey, defaultValue) => {
  it.each([undefined, true, false])("wires an explicit %s setting into spawn defaults", (value) => {
    getConfiguration.mockReturnValue({
      inspect: (setting: string) => setting === key ? { globalValue: value } : undefined,
      get: (setting: string, fallback?: unknown) => setting === key ? value : fallback,
    });
    // This pure mapper uses only VS Code configuration; avoid constructing runtime services.
    const mapper = RuntimeManager.prototype as unknown as {
      extraEnvFromSettings(): Record<string, string>;
    };
    const extraEnv = mapper.extraEnvFromSettings();
    expect(extraEnv[envKey]).toBe(value === undefined ? undefined : String(value));
    const env = buildBackendEnv("/ws", { backend: "gemini", model: "m", extraEnv }, "/rt", "darwin-arm64");
    expect(env[envKey]).toBe(value === undefined ? defaultValue : String(value));
  });
});
