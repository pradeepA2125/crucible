import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { BackendProcess, buildBackendEnv, type ProcessDeps } from "../src/runtime/backend-process.js";

function deps(overrides: Partial<ProcessDeps> = {}) {
  const spawned: { cmd: string; args: string[]; env: Record<string, string> }[] = [];
  const d: ProcessDeps & { spawned: typeof spawned } = {
    runtimeDir: mkdtempSync(join(tmpdir(), "rt-")),
    spawn: (cmd, args, opts) => {
      spawned.push({ cmd, args, env: opts.env });
      return { pid: 4242, kill: () => {}, onExit: () => {} };
    },
    fetchJson: async () => ({ status: "ok", building: false }),
    pickPort: async () => 8123,
    sleep: async () => {},
    isPidAlive: () => false,
    log: () => {},
    platform: "darwin-arm64",
    spawned,
    ...overrides,
  };
  return d;
}

function ws(): string {
  return mkdtempSync(join(tmpdir(), "ws-"));
}

const SETTINGS = {
  backend: "gemini", model: "gemini-flash-latest",
  apiKey: { envVar: "GEMINI_API_KEY", value: "sk-secret" },
};

describe("buildBackendEnv", () => {
  it("assembles the full spawn env", () => {
    const env = buildBackendEnv("/ws", SETTINGS, "/rt", 8123, "darwin-arm64");
    expect(env.AI_EDITOR_REASONING_BACKEND).toBe("gemini");
    expect(env.AI_EDITOR_WORKSPACE_PATH).toBe("/ws");
    expect(env.AI_EDITOR_PORT).toBe("8123");
    expect(env.AI_EDITOR_GEMINI_MODEL).toBe("gemini-flash-latest");
    expect(env.GEMINI_API_KEY).toBe("sk-secret");
    expect(env.AI_EDITOR_RIPGREP_CMD).toBe("/rt/bin/rg");
    expect(env.AI_EDITOR_CHAT_CONTROLLER).toBe("1");
    expect(env.AI_EDITOR_DB_PATH).toBe(join("/ws", ".agentd", "agentd.sqlite3"));
  });
  it("extraEnv overrides defaults; skillsDisabled joins", () => {
    const env = buildBackendEnv("/ws", {
      ...SETTINGS, extraEnv: { AI_EDITOR_SHELL_POLICY: "allow_all" },
      skillsDisabled: ["a", "b"] }, "/rt", 1, "darwin-arm64");
    expect(env.AI_EDITOR_SHELL_POLICY).toBe("allow_all");
    expect(env.AI_EDITOR_SKILLS_DISABLED).toBe("a,b");
  });
});

describe("BackendProcess.start", () => {
  it("reuses a live locked backend without spawning", async () => {
    const w = ws();
    mkdirSync(join(w, ".agentd"));
    writeFileSync(join(w, ".agentd", "agentd.lock"),
      JSON.stringify({ pid: 999, port: 8200, started_at: 1 }));
    const d = deps({ isPidAlive: () => true });
    const res = await new BackendProcess(d).start(w, SETTINGS);
    expect(res).toEqual({ port: 8200, reused: true });
    expect(d.spawned).toHaveLength(0);
  });

  it("reaps a stale lock and spawns backend + watcher", async () => {
    const w = ws();
    mkdirSync(join(w, ".agentd"));
    writeFileSync(join(w, ".agentd", "agentd.lock"),
      JSON.stringify({ pid: 999, port: 8200, started_at: 1 }));
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "ai-editor-indexer"), "");
    const res = await new BackendProcess(d).start(w, SETTINGS);
    expect(res.reused).toBe(false);
    expect(res.port).toBe(8123);
    expect(d.spawned[0].args).toContain("agentd.main:app");
    expect(d.spawned[0].env.AI_EDITOR_PORT).toBe("8123");
    expect(d.spawned[1].args[0]).toBe("index"); // watcher
    expect(d.spawned[1].env.AI_EDITOR_BACKEND_URL).toBe("http://localhost:8123");
  });

  it("skips the watcher when the indexer binary is missing", async () => {
    const d = deps();
    const res = await new BackendProcess(d).start(ws(), SETTINGS);
    expect(res.reused).toBe(false);
    expect(d.spawned).toHaveLength(1); // backend only
  });

  it("throws when health never comes up", async () => {
    const d = deps({ fetchJson: async () => { throw new Error("conn refused"); } });
    await expect(new BackendProcess(d).start(ws(), SETTINGS))
      .rejects.toThrow(/healthy within 60s/);
  });
});
