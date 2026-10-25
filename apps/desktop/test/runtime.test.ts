import { describe, expect, it, vi } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { assertProjectAvailable, assertInstalledLocalModels } from "../src/runtime.js";
import { JsonState } from "../src/store.js";
import { RuntimeCore } from "../../vscode-extension/src/runtime/runtime-core.js";
import { buildBackendEnv } from "../../vscode-extension/src/runtime/backend-process.js";
describe("desktop ownership preflight", () => {
  it("refuses live, malformed and symlink locks without changing them or controlling live processes", async () => {
    const root = mkdtempSync(join(tmpdir(), "crucible-preflight-"));
    try {
      mkdirSync(join(root, ".crucible/state"), { recursive: true }); const path = join(root, ".crucible/state/agentd.lock");
      const deps = { isPidAlive: () => true, fetchRaw: vi.fn(async () => { throw new Error("connection refused"); }), readToken: vi.fn() };
      const lock = JSON.stringify({ pid: 42, port: 8000, started_at: 1 }); writeFileSync(path, lock);
      await expect(assertProjectAvailable(root, deps)).rejects.toThrow("another Crucible client");
      expect(readFileSync(path, "utf8")).toBe(lock); expect(deps.fetchRaw).toHaveBeenCalledTimes(1);
      await expect(assertProjectAvailable(root, deps, 42)).resolves.toBeUndefined();
      writeFileSync(path, "malformed"); await expect(assertProjectAvailable(root, deps)).rejects.toThrow(); expect(readFileSync(path, "utf8")).toBe("malformed");
      rmSync(path); symlinkSync(join(root, "outside"), path); await expect(assertProjectAvailable(root, deps)).rejects.toThrow();
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
  it("permits missing locks and leaves confirmed dead locks for the shared process manager", async () => {
    const root = mkdtempSync(join(tmpdir(), "crucible-preflight-"));
    try {
      const deps: { isPidAlive: () => boolean; fetchRaw: () => Promise<{ status: number; body: string }>; readToken: () => undefined } = { isPidAlive: () => false, fetchRaw: async () => { throw new Error("connection refused"); }, readToken: () => undefined };
      await expect(assertProjectAvailable(root, deps)).resolves.toBeUndefined();
      mkdirSync(join(root, ".crucible/state"), { recursive: true }); const path = join(root, ".crucible/state/agentd.lock");
      writeFileSync(path, JSON.stringify({ pid: 42, port: 8000, started_at: 1 }));
      await expect(assertProjectAvailable(root, deps)).resolves.toBeUndefined();
      deps.fetchRaw = async () => ({ status: 200, body: "{}" });
      // No-token is uncertain, not down: it must not reap a lock it cannot verify.
      await expect(assertProjectAvailable(root, deps)).rejects.toThrow();
      expect(readFileSync(path, "utf8")).toContain('"pid":42');
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
});
describe("desktop runtime defaults", () => {
  it("keeps enabled spawn defaults when preferences are unset and forwards explicit false", async () => {
    const root = mkdtempSync(join(tmpdir(), "crucible-desktop-env-"));
    try {
      const state = new JsonState(join(root, "state.json")); const config = new JsonState(join(root, "preferences.json"));
      const status = { text: "", show() {}, hide() {} };
      const runtime = new RuntimeCore({ runtimeDir: root, resourceDir: root, state,
        secrets: { get: async () => undefined, store: async () => {}, delete: async () => {} }, configuration: () => config, updateGlobalSetting: (key, value) => config.update(key, value),
        output: { append() {}, appendLine() {}, show() {} }, statusBar: status, authStatus: status, showError: async () => undefined });
      await runtime.saveProvider("chatgpt", "model");
      const defaults = (await runtime.getProviderSettings())!;
      expect(defaults.extraEnv).toEqual({});
      const env = buildBackendEnv(root, defaults, root);
      for (const key of ["CRUCIBLE_CHAT_CONTROLLER", "CRUCIBLE_TOOL_BATCHING_ENABLED", "CRUCIBLE_ADAPTIVE_READS_ENABLED", "CRUCIBLE_COMPACT_CONTEXT_ENABLED", "CRUCIBLE_WEB_SEARCH_ENABLED", "CRUCIBLE_WEB_FETCH_ENABLED", "CRUCIBLE_LOCAL_MODELS"]) expect(env[key]).toBe("1");
      expect(env.CRUCIBLE_TASK_SUBSYSTEM).toBe("0"); expect(env.CRUCIBLE_SHELL_POLICY).toBe("ask");
      await config.update("crucible.webFetch.enabled", false);
      expect((await runtime.getProviderSettings())!.extraEnv).toEqual({ CRUCIBLE_WEB_FETCH_ENABLED: "false" });
      await runtime.dispose();
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
});

describe("desktop local-model policy", () => {
  it("refuses a package change before persistence or process restart", () => {
    const runtime = { installedRuntime: () => ({ releaseTag: "test", components: {}, localModels: true }), localModels: () => false };
    expect(() => assertInstalledLocalModels(runtime)).toThrow("installed Crucible runtime");
    expect(() => assertInstalledLocalModels(runtime, false)).toThrow();
    expect(() => assertInstalledLocalModels(runtime, true)).not.toThrow();
  });
});
