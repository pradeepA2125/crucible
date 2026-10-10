import { describe, expect, it, vi } from "vitest";
import { RuntimeManager } from "../src/runtime/vscode-runtime.js";
import { buildBackendEnv } from "../src/runtime/backend-process.js";

const { getConfiguration } = vi.hoisted(() => ({ getConfiguration: vi.fn() }));
vi.mock("vscode", () => ({ workspace: { getConfiguration } }));

describe("web search VS Code settings to managed env", () => {
  it.each([undefined, true, false])("wires an explicit %s setting into spawn defaults", (value) => {
    const key = "crucible.webSearch.enabled";
    getConfiguration.mockReturnValue({
      inspect: (setting: string) => setting === key ? { globalValue: value } : undefined,
      get: (setting: string) => setting === key ? value : undefined,
    });
    // This pure mapper uses only VS Code configuration; avoid constructing runtime services.
    const mapper = RuntimeManager.prototype as unknown as {
      extraEnvFromSettings(): Record<string, string>;
    };
    const extraEnv = mapper.extraEnvFromSettings();
    expect(extraEnv.CRUCIBLE_WEB_SEARCH_ENABLED).toBe(value === undefined ? undefined : String(value));
    const env = buildBackendEnv("/ws", { backend: "gemini", model: "m", extraEnv }, "/rt", "darwin-arm64");
    expect(env.CRUCIBLE_WEB_SEARCH_ENABLED).toBe(value === undefined ? "0" : String(value));
  });
});
