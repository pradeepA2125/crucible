import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { RuntimeCore, type RuntimePorts, type RuntimeState, type RuntimeConfiguration, type ManagedProcess } from "../src/runtime/runtime-core.js";
import { RuntimeUpdateRequiredError, type ChildHandle } from "../src/runtime/backend-process.js";

class MemoryState implements RuntimeState {
  readonly values = new Map<string, unknown>();
  get<T>(key: string): T | undefined;
  get<T>(key: string, fallback: T): T;
  get<T>(key: string, fallback?: T): T | undefined { return this.values.has(key) ? this.values.get(key) as T : fallback; }
  async update(key: string, value: unknown): Promise<void> { this.values.set(key, value); }
}
class Configuration extends MemoryState implements RuntimeConfiguration {
  inspect<T>(key: string) { return this.values.has(key) ? { globalValue: this.get<T>(key) } : undefined; }
}
function fakeProcess(port = 12345, adopted = false) {
  let handle: ChildHandle | undefined;
  let listeners: ((code: number | null) => void)[] = [];
  const kill = vi.fn();
  const process = {
    get backendHandle() { return handle; },
    start: vi.fn<ManagedProcess["start"]>(async () => {
      listeners = [];
      handle = adopted ? undefined : {
        pid: port, kill, onExit: callback => { listeners.push(callback); }, onStdoutLine: () => {}, exited: new Promise(() => {}),
      };
      return { port, reused: adopted };
    }),
    stop: vi.fn(async () => { if (handle) { kill(); process.exit(0); handle = undefined; } }),
    exit: (code: number | null) => { const pending = listeners; listeners = []; for (const listener of pending) listener(code); },
  } satisfies ManagedProcess & { exit(code: number | null): void };
  return { ...process, get backendHandle() { return handle; }, kill };
}
const directories: string[] = [];
afterEach(() => { for (const dir of directories.splice(0)) rmSync(dir, { recursive: true, force: true }); });
function setup(adopted = false) {
  const runtimeDir = mkdtempSync(join(tmpdir(), "crucible-runtime-core-")); directories.push(runtimeDir);
  const state = new MemoryState(); const configuration = new Configuration();
  state.values.set("crucible.provider.backend", "chatgpt"); state.values.set("crucible.provider.model", "model");
  const proc = fakeProcess(12345, adopted);
  const scheduled: { callback: () => void; delay: number }[] = [];
  let clock = 0;
  const statuses: string[] = [];
  const statusBar = { get text() { return statuses.at(-1) ?? ""; }, set text(value: string) { statuses.push(value); }, show: vi.fn(), hide: vi.fn() };
  const ports = {
    runtimeDir, resourceDir: new URL("..", import.meta.url).pathname,
    state, secrets: { get: vi.fn(async (_key: string): Promise<string | undefined> => undefined), store: vi.fn(async () => {}), delete: vi.fn(async () => {}) },
    configuration: () => configuration, updateGlobalSetting: vi.fn(async () => {}),
    output: { append: vi.fn(), appendLine: vi.fn(), show: vi.fn() }, statusBar,
    authStatus: { text: "", tooltip: undefined as unknown, show: vi.fn(), hide: vi.fn() },
    showError: vi.fn<RuntimePorts["showError"]>(async () => undefined),
    now: () => clock, schedule: (callback: () => void, delay: number) => { scheduled.push({ callback, delay }); },
    createProcess: vi.fn<NonNullable<RuntimePorts["createProcess"]>>(() => proc),
  } satisfies RuntimePorts;
  // URL pathname percent-encodes the workspace space; the test resource path must be native.
  ports.resourceDir = new URL("../", import.meta.url).pathname.replaceAll("%20", " ");
  const core = new RuntimeCore(ports);
  return { core, ports, state, configuration, proc, statuses, scheduled, advance: (ms: number) => { clock += ms; } };
}

describe("shared runtime orchestration", () => {
  it("publishes starting/ready and keeps backend-ready routing scoped to the workspace", async () => {
    const h = setup(); const ready = vi.fn(); h.core.onBackendReady(ready);
    await expect(h.core.startForWorkspace("one")).resolves.toEqual({ port: 12345, reused: false });
    expect(h.statuses).toEqual(["$(rocket) Crucible: starting…", "$(check) Crucible owner :12345"]);
    expect(h.core.backendUrl("one")).toBe("http://127.0.0.1:12345");
    expect(h.core.backendUrl("other")).toBeUndefined(); expect(ready).toHaveBeenCalledExactlyOnceWith("one");
  });

  it("preserves adopted backend reuse and installs no child crash watcher", async () => {
    const h = setup(true);
    await expect(h.core.startForWorkspace("one")).resolves.toEqual({ port: 12345, reused: true });
    h.proc.exit(1); expect(h.scheduled).toEqual([]);
    await h.core.restart("one");
    expect(h.proc.start).toHaveBeenCalledTimes(2);
    expect(h.ports.createProcess).toHaveBeenCalledOnce();
    expect(h.core.backendUrl("one")).toBe("http://127.0.0.1:12345");
    expect(h.proc.kill).not.toHaveBeenCalled(); // Preserved adopted-restart quirk, not fixed here.
  });

  it("backs off 2/4/8 seconds and fails after the maximum restart attempts", async () => {
    const h = setup(); await h.core.startForWorkspace("one");
    for (const delay of [2000, 4000, 8000]) {
      const previous = h.proc.start.mock.calls.length;
      h.proc.exit(1); const job = h.scheduled.shift()!; expect(job.delay).toBe(delay);
      job.callback(); await vi.waitFor(() => expect(h.proc.start).toHaveBeenCalledTimes(previous + 1));
    }
    h.proc.exit(1);
    expect(h.scheduled).toEqual([]);
    expect(h.statuses.at(-1)).toBe("$(error) Crucible failed");
    expect(h.ports.showError).toHaveBeenCalledWith("Crucible backend failed: backend keeps crashing", false, ["Open logs"]);
  });

  it("resets backoff after a backend stays healthy longer than the reset window", async () => {
    const h = setup(); await h.core.startForWorkspace("one"); h.proc.exit(1);
    h.scheduled.shift()!.callback(); await vi.waitFor(() => expect(h.proc.start).toHaveBeenCalledTimes(2));
    h.advance(5 * 60_000 + 1); h.proc.exit(1);
    expect(h.scheduled[0]?.delay).toBe(2000);
  });

  it("suppresses intentional-stop restarts and cancels scheduled work after disposal", async () => {
    const h = setup(); await h.core.startForWorkspace("one");
    await h.core.restart("one"); expect(h.scheduled).toEqual([]);
    h.proc.exit(1); const job = h.scheduled.shift()!;
    await h.core.dispose(); job.callback(); await Promise.resolve();
    expect(h.proc.start).toHaveBeenCalledTimes(2); expect(h.core.backendUrl("one")).toBeUndefined();
  });

  it("shows the runtime-update state/modal and preserves the thrown update error", async () => {
    const h = setup(); const error = new RuntimeUpdateRequiredError("agentd", "requires update");
    h.proc.start.mockRejectedValueOnce(error);
    await expect(h.core.startForWorkspace("one")).rejects.toBe(error);
    expect(h.statuses.at(-1)).toBe("$(error) Crucible: runtime update required");
    expect(h.ports.showError).toHaveBeenCalledWith(`${error.message}.`, true, ["Update runtime"]);
  });

  it("delegates disposal to each process's ownership-aware stop", async () => {
    const h = setup(); const adopted = fakeProcess(23456, true);
    h.ports.createProcess.mockReturnValueOnce(h.proc).mockReturnValueOnce(adopted);
    await h.core.startForWorkspace("owned"); await h.core.startForWorkspace("adopted");
    await h.core.dispose();
    expect(h.proc.kill).toHaveBeenCalledOnce(); expect(adopted.kill).not.toHaveBeenCalled();
    expect(h.proc.stop).toHaveBeenCalledOnce(); expect(adopted.stop).toHaveBeenCalledOnce();
  });

  it("keeps persisted keys, explicit setting precedence and both settings-write ports", async () => {
    const h = setup();
    await h.core.saveProvider("chatgpt", "chosen"); await h.core.saveContextWindow(200000);
    await h.core.saveReasoningEffort("high"); await h.core.saveChatGPTRegistration("account");
    await h.core.saveClaudeRegistration("claude-account"); await h.core.markChatGPTWelcomed("account");
    await h.core.setSkillsDisabled(["disabled-skill"]); await h.core.setMcpDisabled(["disabled-server"]);
    await h.core.setPlanMode(true); await h.core.setStepReview(false); await h.core.setLocalModels(false);
    expect(h.ports.updateGlobalSetting).toHaveBeenCalledExactlyOnceWith("crucible.localModels", false);
    expect(h.core.getPlanMode()).toBe(true); expect(h.core.getStepReview()).toBe(false);
    const settings = await h.core.getProviderSettings();
    expect(settings).toMatchObject({ backend: "chatgpt", model: "chosen", contextWindow: 200000,
      reasoningEffort: "high", skillsDisabled: ["disabled-skill"], localModels: false,
      extraEnv: { CRUCIBLE_CHATGPT_REGISTRATION: "account" } });
    h.configuration.values.set("crucible.localModels", true); expect(h.core.localModels()).toBe(true);
    expect(h.state.values.get("crucible.chatgpt.welcomed")).toEqual(["account"]);
  });

  it("clears and reports auth status through its own presentation port", () => {
    const h = setup(); h.core.setAuthError("sign in again");
    expect(h.ports.authStatus.tooltip).toBe("sign in again"); expect(h.ports.authStatus.show).toHaveBeenCalledOnce();
    h.core.setAuthError(null); expect(h.ports.authStatus.hide).toHaveBeenCalledOnce();
  });
});

it("idle pause never signals observers and invalidates an already scheduled crash retry",async()=>{
 const h=setup();await h.core.startForWorkspace("one");h.proc.exit(1);expect(h.scheduled).toHaveLength(1);expect(await h.core.pause("one")).toBe(true);h.scheduled[0]!.callback();await Promise.resolve();expect(h.proc.start).toHaveBeenCalledOnce();expect(h.core.backendUrl("one")).toBeUndefined();
 const observer=setup(true);await observer.core.startForWorkspace("two");expect(await observer.core.pause("two")).toBe(false);expect(observer.proc.stop).not.toHaveBeenCalled();
});
