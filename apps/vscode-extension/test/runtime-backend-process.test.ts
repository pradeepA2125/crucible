import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { healthBound, healthProof } from "../src/runtime/probe-health.js";
import { BackendProcess, buildBackendEnv, finalSpawnEnv, RuntimeUpdateRequiredError,
  type ChildHandle, type ProcessDeps } from "../src/runtime/backend-process.js";

const TOKEN = "k".repeat(43);

function stubChild(pid: number, handshake: { pid: number; port: number } | null): ChildHandle & { killed: string[] } {
  let resolveExit: (code: number | null) => void = () => {};
  const exited = new Promise<number | null>((r) => { resolveExit = r; });
  const killed: string[] = [];
  return {
    pid, killed, exited,
    kill: (sig) => { killed.push(sig ?? "SIGTERM"); resolveExit(0); },
    onExit: (cb) => { void exited.then(cb); },
    onStdoutLine: (cb) => {
      if (handshake) cb(`CRUCIBLE_SERVE ${JSON.stringify(handshake)}`);
    },
  };
}

function deps(overrides: Partial<ProcessDeps> = {}, child = stubChild(4242, { pid: 4242, port: 8123 })) {
  const spawned: { cmd: string; args: string[]; env: Record<string, string>; cwd?: string }[] = [];
  const d: ProcessDeps & { spawned: typeof spawned } = {
    runtimeDir: mkdtempSync(join(tmpdir(), "rt-")),
    spawn: (cmd, args, opts) => {
      spawned.push({ cmd, args, env: opts.env, cwd: opts.cwd });
      return spawned.length === 1 ? child : stubChild(5000 + spawned.length, null);
    },
    fetchJson: async () => ({ status: "ok", building: false }),
    // A /health that proves the token for whichever pid/workspace the caller expects.
    fetchRaw: async (url) => {
      const nonce = new URL(url).searchParams.get("nonce") ?? "";
      return { status: 200, body: JSON.stringify({ status: "ok", pid: 4242,
        proof: healthProof(TOKEN, nonce), bound: healthBound(TOKEN, nonce, 4242, currentWs) }) };
    },
    readToken: () => TOKEN,
    processInfo: async () => null,
    signal: () => {},
    exec: async () => ({ code: 0, stdout: "0.1.0 auth=1", stderr: "", timedOut: false }),
    now: () => Date.now(),
    sleep: async () => {},
    isPidAlive: () => false,
    log: () => {},
    platform: "darwin-arm64",
    uid: 501,
    spawned,
    ...overrides,
  };
  return d;
}

let currentWs = "";
function ws(): string {
  currentWs = mkdtempSync(join(tmpdir(), "ws-"));
  return currentWs;
}

const SETTINGS = {
  backend: "gemini", model: "gemini-flash-latest",
  apiKey: { envVar: "GEMINI_API_KEY", value: "sk-secret" },
};

describe("buildBackendEnv", () => {
  it("assembles the full spawn env", () => {
    const env = buildBackendEnv("/ws", SETTINGS, "/rt", "darwin-arm64");
    expect(env.CRUCIBLE_REASONING_BACKEND).toBe("gemini");
    expect(env.CRUCIBLE_WORKSPACE_PATH).toBe("/ws");
    expect(env.CRUCIBLE_PORT).toBeUndefined();
    expect(env.CRUCIBLE_GEMINI_MODEL).toBe("gemini-flash-latest");
    expect(env.GEMINI_API_KEY).toBe("sk-secret");
    expect(env.CRUCIBLE_RIPGREP_CMD).toBe("/rt/bin/rg");
    expect(env.CRUCIBLE_CHAT_CONTROLLER).toBe("1");
    expect(env.CRUCIBLE_EXEC_SESSIONS_ENABLED).toBe("1");
    expect(env.CRUCIBLE_PROVIDER_MAX_RPM).toBe("35");
    expect(env.CRUCIBLE_DB_PATH).toBe(join("/ws", ".crucible/state", "agentd.sqlite3"));
  });
  it("sets every relative-path default the backend reads, absolute and workspace-rooted", () => {
    // Regression: CRUCIBLE_MEMORY_DB_PATH was never set here, so it fell back to
    // MemoryConfig's relative default (.crucible/state/memory.sqlite3), resolved
    // against whatever cwd the extension host happened to have — not the
    // workspace — and crashed with "Read-only file system" on activation.
    const env = buildBackendEnv("/ws", SETTINGS, "/rt", "darwin-arm64");
    expect(env.CRUCIBLE_MEMORY_DB_PATH).toBe(join("/ws", ".crucible/state", "memory.sqlite3"));
    expect(env.CRUCIBLE_VECTOR_INDEX_PATH).toBe(join("/ws", ".crucible", "vector-index"));
    expect(env.CRUCIBLE_LOG_DIR).toBe(join("/ws", ".tmp", "reasoning"));
  });
  it("extraEnv overrides defaults; skillsDisabled joins", () => {
    const env = buildBackendEnv("/ws", {
      ...SETTINGS, extraEnv: { CRUCIBLE_SHELL_POLICY: "allow_all" },
      skillsDisabled: ["a", "b"] }, "/rt", "darwin-arm64");
    expect(env.CRUCIBLE_SHELL_POLICY).toBe("allow_all");
    expect(env.CRUCIBLE_SKILLS_DISABLED).toBe("a,b");
  });
  it("sets CRUCIBLE_OPENAI_COMPAT_MODEL and threads the base URL through extraEnv, no API key", () => {
    // openai_compatible's API key is optional (keyless local servers like vLLM/LM
    // Studio) — settings.apiKey is simply absent, no *_API_KEY var should appear.
    // The Base URL extra field rides the generic extraEnv path (same as watsonx's
    // WATSONX_SPACE_ID/WATSONX_URL) — no bespoke wiring needed here.
    const env = buildBackendEnv("/ws", {
      backend: "openai_compatible", model: "llama-3.3-70b",
      extraEnv: { CRUCIBLE_OPENAI_COMPAT_BASE_URL: "http://localhost:8000/v1" },
    }, "/rt", "darwin-arm64");
    expect(env.CRUCIBLE_OPENAI_COMPAT_MODEL).toBe("llama-3.3-70b");
    expect(env.CRUCIBLE_OPENAI_COMPAT_BASE_URL).toBe("http://localhost:8000/v1");
    expect(env.CRUCIBLE_OPENAI_COMPAT_API_KEY).toBeUndefined();
  });
});

describe("BackendProcess.start", () => {
  it("spawns agentd.serve, takes the port from the handshake, starts the watcher", async () => {
    const d = deps();
    const w = ws();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    const r = await new BackendProcess(d).start(w, SETTINGS);
    expect(r).toEqual({ port: 8123, reused: false });
    expect(d.spawned[0].args).toEqual(["-m", "agentd.serve", "--port", "0", "--workspace-lock", w]);
    expect(d.spawned[0].env.CRUCIBLE_PORT).toBeUndefined();
    // Regression: without an explicit cwd, Node defaults to the calling process's
    // cwd (the VS Code extension host's, not the workspace).
    expect(d.spawned[0].cwd).toBe(w);
    expect(d.spawned[1].args[0]).toBe("index"); // watcher
    expect(d.spawned[1].env.CRUCIBLE_BACKEND_URL).toBe("http://127.0.0.1:8123");
    expect(d.spawned[1].cwd).toBe(w);
  });

  it("skips the watcher when the indexer binary is missing", async () => {
    const d = deps();
    const res = await new BackendProcess(d).start(ws(), SETTINGS);
    expect(res.reused).toBe(false);
    expect(d.spawned).toHaveLength(1); // backend only
  });

  it("sets CRUCIBLE_LSP_RS_CMD to the managed binary when installed", async () => {
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    writeFileSync(join(d.runtimeDir, "bin", "rust-analyzer"), "");
    await new BackendProcess(d).start(ws(), SETTINGS);
    expect(d.spawned[1].env.CRUCIBLE_LSP_RS_CMD).toBe(join(d.runtimeDir, "bin", "rust-analyzer"));
  });

  it("sets generous LSP startup/request timeouts for the watcher (indexer's own default is 3s, too tight for a cold gopls spawn — found live)", async () => {
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    await new BackendProcess(d).start(ws(), SETTINGS);
    expect(d.spawned[1].env.CRUCIBLE_LSP_STARTUP_TIMEOUT_MS).toBe("180000");
    expect(d.spawned[1].env.CRUCIBLE_LSP_REQUEST_TIMEOUT_MS).toBe("20000");
  });

  it("falls back to the bare rust-analyzer command when the managed binary is absent", async () => {
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    await new BackendProcess(d).start(ws(), SETTINGS);
    expect(d.spawned[1].env.CRUCIBLE_LSP_RS_CMD).toBe("rust-analyzer");
  });

  it("sets CRUCIBLE_LSP_GO_CMD to the managed binary when installed", async () => {
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    writeFileSync(join(d.runtimeDir, "bin", "gopls"), "");
    await new BackendProcess(d).start(ws(), SETTINGS);
    expect(d.spawned[1].env.CRUCIBLE_LSP_GO_CMD).toBe(join(d.runtimeDir, "bin", "gopls"));
  });

  it("falls back to the bare gopls command when the managed binary is absent", async () => {
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    await new BackendProcess(d).start(ws(), SETTINGS);
    expect(d.spawned[1].env.CRUCIBLE_LSP_GO_CMD).toBe("gopls");
  });

  it("sets CRUCIBLE_LSP_JAVA_CMD when the managed JRE and jdtls both landed", async () => {
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    mkdirSync(join(d.runtimeDir, "jre", "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "jre", "bin", "java"), "");
    mkdirSync(join(d.runtimeDir, "jdtls", "plugins"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "jdtls", "plugins", "org.eclipse.equinox.launcher_1.7.200.jar"), "");
    mkdirSync(join(d.runtimeDir, "jdtls", "config_mac_arm"), { recursive: true });

    const workspace = ws();
    await new BackendProcess(d).start(workspace, SETTINGS);
    const javaCmd = d.spawned[1].env.CRUCIBLE_LSP_JAVA_CMD;
    expect(javaCmd).toBeDefined();
    expect(javaCmd).toContain(join(d.runtimeDir, "jre", "bin", "java"));
    expect(javaCmd).toContain("-jar");
    expect(javaCmd).toContain(join(d.runtimeDir, "jdtls", "plugins", "org.eclipse.equinox.launcher_1.7.200.jar"));
    expect(javaCmd).toContain(join(d.runtimeDir, "jdtls", "config_mac_arm"));
    expect(javaCmd).toContain("jdtls-data");
  });

  it("omits CRUCIBLE_LSP_JAVA_CMD (falls back to bare jdtls) when the managed JRE/jdtls are absent", async () => {
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    await new BackendProcess(d).start(ws(), SETTINGS);
    expect(d.spawned[1].env.CRUCIBLE_LSP_JAVA_CMD).toBeUndefined();
  });

  it("throws when health never comes up", async () => {
    const child = stubChild(4242, { pid: 4242, port: 8123 });
    const d = deps({ fetchRaw: async () => { throw new TypeError("fetch failed"); } }, child);
    await expect(new BackendProcess(d).start(ws(), SETTINGS))
      .rejects.toThrow(/healthy within 60s/);
    expect(child.killed).toContain("SIGTERM");
  });
});

describe("context window in the spawn env", () => {
  it("writes CRUCIBLE_MEMORY_WINDOW_TOKENS when a window is declared", () => {
    const env = buildBackendEnv(
      "/ws",
      { backend: "groq", model: "m", contextWindow: 32768 },
      "/rt", "darwin-arm64",
    );
    expect(env.CRUCIBLE_MEMORY_WINDOW_TOKENS).toBe("32768");
  });

  it("omits it entirely when none is declared", () => {
    /* A deployment that sets only CRUCIBLE_MEMORY_WINDOW_TOKENS by hand must keep
       working unchanged — writing a default here would silently override it. */
    const env = buildBackendEnv(
      "/ws", { backend: "groq", model: "m" }, "/rt", "darwin-arm64",
    );
    expect("CRUCIBLE_MEMORY_WINDOW_TOKENS" in env).toBe(false);
  });

  it("does not swallow 0 as a falsy value", () => {
    /* 0 is NOT a valid declared window — routes.py floors it at 1024, and a 0
       reaching MemoryConfig would make every turn compact forever. This test
       only pins that the guard here is `!== undefined`, not truthiness: this
       function's job is to pass contextWindow through unmodified when it was
       explicitly set, and let the backend own the floor, not to validate the
       number itself. */
    const env = buildBackendEnv(
      "/ws", { backend: "groq", model: "m", contextWindow: 0 }, "/rt", "darwin-arm64",
    );
    expect(env.CRUCIBLE_MEMORY_WINDOW_TOKENS).toBe("0");
  });

  it("allows extraEnv to override the built-in window", () => {
    /* The spread order {...built, ...settings.extraEnv} means extraEnv wins. */
    const env = buildBackendEnv(
      "/ws",
      {
        backend: "groq", model: "m", contextWindow: 32768,
        extraEnv: { CRUCIBLE_MEMORY_WINDOW_TOKENS: "999999" },
      },
      "/rt", "darwin-arm64",
    );
    expect(env.CRUCIBLE_MEMORY_WINDOW_TOKENS).toBe("999999");
  });
});

describe("spawn handshake", () => {
  it("fails fast when the child exits before printing the handshake", async () => {
    const child = stubChild(4242, null);
    const d = deps({}, child);
    // Exited before start() subscribes: the stub's instant sleep would otherwise
    // fire the 10 s handshake timeout first.
    child.kill("SIGTERM");
    await expect(new BackendProcess(d).start(ws(), SETTINGS)).rejects.toThrow(/exited/);
  });

  it("strips CRUCIBLE_AUTH_DISABLED from the env actually spawned, even from extraEnv", async () => {
    process.env.CRUCIBLE_AUTH_DISABLED = "1";
    try {
      const d = deps();
      await new BackendProcess(d).start(ws(), { ...SETTINGS,
        extraEnv: { CRUCIBLE_AUTH_DISABLED: "1" } });
      for (const s of d.spawned) expect(s.env.CRUCIBLE_AUTH_DISABLED).toBeUndefined();
    } finally {
      delete process.env.CRUCIBLE_AUTH_DISABLED;
    }
  });

  it("a backend it just spawned that answers without a proof needs a runtime update", async () => {
    const d = deps({ fetchRaw: async () => ({ status: 200, body: '{"status":"ok"}' }) });
    await expect(new BackendProcess(d).start(ws(), SETTINGS))
      .rejects.toBeInstanceOf(RuntimeUpdateRequiredError);
  });
});

describe("finalSpawnEnv", () => {
  it("drops undefined and CRUCIBLE_AUTH_DISABLED", () => {
    expect(finalSpawnEnv({ A: "1", B: undefined }, { CRUCIBLE_AUTH_DISABLED: "1", C: "3" }))
      .toEqual({ A: "1", C: "3" });
  });
});
