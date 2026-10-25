import { describe, expect, it, vi } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
const child = vi.hoisted(() => ({ start: vi.fn(), stop: vi.fn(), onExit: vi.fn() }));
vi.mock("../../vscode-extension/src/runtime/backend-process.js", async load => {
  const actual = await load<Record<string, unknown>>();
  return { ...actual, BackendProcess: class {
    backendHandle = { pid: 42, onExit: child.onExit };
    start = child.start; stop = child.stop;
  } };
});
import { createDesktopRuntime } from "../src/runtime.js";
import { JsonState } from "../src/store.js";
describe("runtime package-mutation backstop", () => {
  it("filters legacy stored extra credentials before spawn and clamps old shell policy", async () => {
    const directory = mkdtempSync(join(tmpdir(), "crucible-legacy-policy-"));
    try {
      const state = new JsonState(join(directory, "state.json")); const preferences = new JsonState(join(directory, "preferences.json"));
      await state.update("crucible.provider.backend", "watsonx"); await state.update("crucible.provider.model", "model");
      await state.update("crucible.provider.extraEnvVars.watsonx", ["PYTHONPATH", "NODE_OPTIONS", "CRUCIBLE_TASK_SUBSYSTEM", "CRUCIBLE_SHELL_POLICY", "CRUCIBLE_OPENAI_COMPAT_BASE_URL", "WATSONX_URL"]);
      await preferences.update("crucible.policy.shell", "allow_all");
      const readSecret = vi.fn(async (key: string) => key.endsWith("WATSONX_URL") ? "https://example.com" : undefined);
      const status = { text: "", show() {}, hide() {} };
      const runtime = createDesktopRuntime({ runtimeDir: directory, resourceDir: directory, state, configuration: () => preferences,
        updateGlobalSetting: (key, value) => preferences.update(key, value), secrets: { get: readSecret, store: async () => {}, delete: async () => {} },
        statusBar: status, authStatus: status, output: { append() {}, appendLine() {}, show() {} }, showError: async () => undefined });
      const settings = (await runtime.getProviderSettings())!;
      expect(settings.extraEnv).toEqual({ CRUCIBLE_SHELL_POLICY: "ask", WATSONX_URL: "https://example.com" });
      expect(readSecret.mock.calls.map(([key]) => key)).toEqual(["crucible.providerKey.watsonx", "crucible.providerExtra.watsonx.WATSONX_URL"]);
      // Reading never rewrites old state, and a denied interpreter field is never decrypted.
      expect(preferences.get("crucible.policy.shell")).toBe("allow_all");
      await runtime.dispose();
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });
  it("leaves a running child untouched when a mismatched preference is encountered", async () => {
    const directory = mkdtempSync(join(tmpdir(), "crucible-runtime-policy-"));
    try {
      const root = join(directory, "project"); mkdirSync(root);
      writeFileSync(join(directory, "runtime.json"), JSON.stringify({ releaseTag: "test", components: {}, localModels: true }));
      const state = new JsonState(join(directory, "state.json")); const preferences = new JsonState(join(directory, "preferences.json"));
      const status = { text: "", show() {}, hide() {} };
      child.start.mockResolvedValue({ port: 8000, reused: false }); child.stop.mockResolvedValue(undefined);
      const runtime = createDesktopRuntime({ runtimeDir: directory, resourceDir: directory, state,
        configuration: () => preferences, updateGlobalSetting: (key, value) => preferences.update(key, value),
        secrets: { get: async () => undefined, store: async () => {}, delete: async () => {} },
        statusBar: status, authStatus: status, output: { append() {}, appendLine() {}, show() {} }, showError: async () => undefined });
      await runtime.saveProvider("chatgpt", "model"); await runtime.startForWorkspace(root);
      await preferences.update("crucible.localModels", false);
      await expect(runtime.restart(root)).rejects.toThrow("installed Crucible runtime");
      await expect(runtime.startForWorkspace(root)).rejects.toThrow("installed Crucible runtime");
      expect(child.stop).not.toHaveBeenCalled(); expect(child.start).toHaveBeenCalledTimes(1);
      await runtime.dispose(); expect(child.stop).toHaveBeenCalledTimes(1);
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });
});
