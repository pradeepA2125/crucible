import { describe, expect, it, vi } from "vitest";
import * as vscode from "vscode";
import { RuntimeManager } from "../src/runtime/vscode-runtime.js";
import { buildBackendEnv } from "../src/runtime/backend-process.js";

vi.mock("vscode", () => ({ workspace: { getConfiguration: vi.fn() } }));

describe("managed local-model package choice", () => {
  it.each([true, false])("sends an explicit shared capability choice: %s", localModels => {
    const env = buildBackendEnv("/ws", { backend: "gemini", model: "m", localModels }, "/rt");
    expect(env.CRUCIBLE_LOCAL_MODELS).toBe(localModels ? "1" : "0");
    expect(env.CRUCIBLE_SEMANTIC_RETRIEVAL).toBeUndefined();
  });

  it("stops every shared backend before changing packages, then resumes other workspaces", async () => {
    const events: string[] = [];
    const runtime = {
      installedRuntime: () => ({ localModels: true }),
      localModels: () => false,
      processes: new Map(["a", "b"].map(name => [
        name, { stop: async () => { events.push("stop " + name); } },
      ])),
      intentionalStops: new Set<string>(),
      restartAttempts: new Map<string, number>(),
      output: { appendLine: vi.fn() },
      install: async () => {
        expect(runtime.intentionalStops).toEqual(new Set(["a", "b"]));
        events.push("install");
        return { ok: true };
      },
      startForWorkspace: async (name: string) => { events.push("start " + name); },
    };
    const prototype = RuntimeManager.prototype as unknown as {
      reconcileLocalModelPackages(this: object, workspace: string): Promise<void>;
    };
    await prototype.reconcileLocalModelPackages.call(runtime, "a");
    expect(events).toEqual(["stop a", "stop b", "install", "start b"]);
    expect(runtime.intentionalStops.size).toBe(0);
  });
});

describe("workspace experimental memory settings", () => {
  const readEnvironment = (values: Record<string, unknown>) => {
    const cfg = {
      inspect: (key: string) => key in values ? { workspaceValue: values[key] } : undefined,
      get: (key: string, fallback?: unknown) => values[key] ?? fallback,
    };
    vi.mocked(vscode.workspace.getConfiguration)
      .mockReturnValue(cfg as unknown as vscode.WorkspaceConfiguration);
    const prototype = RuntimeManager.prototype as unknown as {
      extraEnvFromSettings(this: object): Record<string, string>;
    };
    return prototype.extraEnvFromSettings.call({});
  };

  it("passes explicit workspace flags and an isolated durable store into the managed spawn", () => {
    const extraEnv = readEnvironment({
      "crucible.memory.enabled": true,
      "crucible.memory.experimental.recall": true,
      "crucible.memory.experimental.curator": true,
      "crucible.memory.experimental.compaction": true,
      "crucible.memory.experimental.storePath": "/ws/.crucible/state/memory-v2-local.sqlite3",
    });
    const env = buildBackendEnv("/ws", { backend: "chatgpt", model: "m", extraEnv }, "/rt");
    expect(env.CRUCIBLE_MEMORY_ENABLED).toBe("true");
    expect(env.CRUCIBLE_MEMORY_RECALL_V2).toBe("true");
    expect(env.CRUCIBLE_MEMORY_CURATOR_V2).toBe("true");
    expect(env.CRUCIBLE_MEMORY_COMPACT_EVIDENCE).toBe("true");
    expect(env.CRUCIBLE_MEMORY_STORE_PATH).toBe("/ws/.crucible/state/memory-v2-local.sqlite3");
  });

  it("leaves unset gates and an empty store override absent, and preserves explicit opt out", () => {
    expect(readEnvironment({ "crucible.memory.experimental.storePath": " " })).toEqual({});
    expect(readEnvironment({ "crucible.memory.experimental.recall": false }))
      .toEqual({ CRUCIBLE_MEMORY_RECALL_V2: "false" });
  });
});
