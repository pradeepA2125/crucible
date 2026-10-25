import type { BackendConfig, ClaudeModel } from "@crucible/editor-client";
import { describe, expect, it, vi } from "vitest";
import { createComposerModelHost, type ComposerModelPorts } from "../src/composer-model-host.js";

type Client = ReturnType<ComposerModelPorts["client"]>;
function setup() {
  let clock = 0;
  let config: Pick<BackendConfig, "provider"> = { provider: {
    backend: "claude_subscription", model: "current", usesChatgptPlan: false,
    reasoningEffort: "low", reasoningEffortNote: "Provider applied Low",
  } };
  const client = {
    getConfig: vi.fn<Client["getConfig"]>(async () => config),
    listChatGPTModels: vi.fn<Client["listChatGPTModels"]>(async () => [{ slug: "chat", displayName: "Chat", contextWindow: 200000 }]),
    listClaudeModels: vi.fn<Client["listClaudeModels"]>(async () => [
      { slug: "current", displayName: "Current", contextWindow: 200000, maxTokens: 10000 },
      { slug: "unknown", displayName: "Unverified", contextWindow: null, maxTokens: null },
    ]),
    setProvider: vi.fn<Client["setProvider"]>(async () => ({ backend: "claude_subscription", model: "current", contextWindow: 200000, reasoningEffort: "low" })),
  } satisfies Client;
  const runtime = {
    chatgptRegistration: () => "chat-account", claudeRegistration: () => "claude-account",
    getProviderKey: vi.fn(async (_backend: string): Promise<string | undefined> => undefined),
    saveProvider: vi.fn(async (_backend: string, _model: string) => {}),
    saveContextWindow: vi.fn(async (_tokens: number) => {}),
    saveReasoningEffort: vi.fn(async (_level: string | undefined) => {}),
  } satisfies ComposerModelPorts["runtime"];
  const refreshContextUsage = vi.fn(async () => {});
  const refreshModels = vi.fn(async () => {});
  const host = createComposerModelHost({ client: () => client, runtime, refreshContextUsage, refreshModels, now: () => clock });
  return { host, client, runtime, refreshContextUsage, refreshModels,
    advanceClock: (ms: number) => { clock += ms; }, setConfig: (value: typeof config) => { config = value; } };
}

describe("shared composer provider coordinator", () => {
  it("uses account catalogs and preserves the durable backend effort note", async () => {
    const h = setup();
    const state = await h.host.state();
    expect(state.usesClaudePlan).toBe(true);
    expect(state.effort).toMatchObject({ level: "low", note: "Provider applied Low" });
    expect(state.options).toContainEqual(expect.objectContaining({ backend: "claude_subscription", model: "current", active: true }));
    expect(state.options).not.toContainEqual(expect.objectContaining({ model: "unknown" }));
    expect(h.client.listClaudeModels).toHaveBeenCalledWith("claude-account");
  });

  it("caches both catalogs and expires them using the host clock", async () => {
    const h = setup();
    await h.host.state(); await h.host.state();
    expect(h.client.listChatGPTModels).toHaveBeenCalledTimes(1);
    expect(h.client.listClaudeModels).toHaveBeenCalledTimes(1);
    h.advanceClock(5 * 60_000);
    await h.host.state();
    expect(h.client.listChatGPTModels).toHaveBeenCalledTimes(2);
    expect(h.client.listClaudeModels).toHaveBeenCalledTimes(2);
  });

  it("preserves the existing provider/auth invalidation scopes and refreshes both views", async () => {
    const h = setup();
    await h.host.state();
    await h.host.afterSettingsMutation("provider"); await h.host.state();
    expect(h.client.listChatGPTModels).toHaveBeenCalledTimes(1);
    expect(h.client.listClaudeModels).toHaveBeenCalledTimes(2);
    await h.host.afterSettingsMutation("auth"); await h.host.state();
    expect(h.client.listChatGPTModels).toHaveBeenCalledTimes(2);
    expect(h.client.listClaudeModels).toHaveBeenCalledTimes(3);
    expect(h.refreshContextUsage).toHaveBeenCalledTimes(2);
    expect(h.refreshModels).toHaveBeenCalledTimes(2);
  });

  it("does not cache or show a Claude response completed after auth invalidation", async () => {
    const h = setup();
    let finish!: (value: ClaudeModel[]) => void;
    h.client.listClaudeModels.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const stale = h.host.state();
    await vi.waitFor(() => expect(h.client.listClaudeModels).toHaveBeenCalledOnce());
    await h.host.afterSettingsMutation("auth");
    await h.host.state();
    finish([{ slug: "stale", displayName: "Stale", contextWindow: 128000, maxTokens: 10000 }]);
    expect((await stale).options).not.toContainEqual(expect.objectContaining({ model: "stale" }));
    await h.host.state();
    expect(h.client.listClaudeModels).toHaveBeenCalledTimes(2);
  });

  it("sends the live model/account when changing effort and persists the applied rung", async () => {
    const h = setup();
    const state = await h.host.setReasoningEffort("max");
    expect(h.client.setProvider).toHaveBeenCalledExactlyOnceWith({ backend: "claude_subscription", model: "current",
      credentials: { CRUCIBLE_CLAUDE_REGISTRATION: "claude-account" }, reasoningEffort: "max" });
    expect(h.runtime.saveReasoningEffort).toHaveBeenCalledExactlyOnceWith("low");
    expect(state.effort.note).toBe("Provider applied Low");
  });

  it("clears persisted effort when the backend applies a cleared override", async () => {
    const h = setup();
    h.client.setProvider.mockResolvedValueOnce({ backend: "claude_subscription", model: "current", reasoningEffort: null });
    await h.host.setReasoningEffort(null);
    expect(h.runtime.saveReasoningEffort).toHaveBeenCalledExactlyOnceWith(undefined);
    expect(h.client.setProvider).toHaveBeenCalledWith(expect.objectContaining({ reasoningEffort: null, model: "current" }));
  });

  it("does not persist an effort request rejected by the backend", async () => {
    const h = setup();
    h.client.setProvider.mockRejectedValueOnce(new Error("effort unavailable"));
    await expect(h.host.setReasoningEffort("high")).rejects.toThrow("effort unavailable");
    expect(h.runtime.saveReasoningEffort).not.toHaveBeenCalled();
  });

  it("does not attempt a provider write when no live provider is configured", async () => {
    const h = setup(); h.setConfig({ provider: null });
    await expect(h.host.setReasoningEffort("high")).rejects.toThrow("no provider configured");
    expect(h.client.setProvider).not.toHaveBeenCalled();
  });

  it("persists the Claude window returned by the backend after a successful swap", async () => {
    const h = setup();
    await h.host.setModel("claude_subscription", "current");
    expect(h.client.setProvider).toHaveBeenCalledWith({ backend: "claude_subscription", model: "current", credentials: { CRUCIBLE_CLAUDE_REGISTRATION: "claude-account" } });
    expect(h.runtime.saveProvider).toHaveBeenCalledExactlyOnceWith("claude_subscription", "current");
    expect(h.runtime.saveContextWindow).toHaveBeenCalledExactlyOnceWith(200000);
    expect(h.refreshContextUsage).toHaveBeenCalledOnce();
  });

  it("keeps the active model visible if optional catalog discovery fails", async () => {
    const h = setup();
    h.client.listClaudeModels.mockRejectedValueOnce(new Error("catalog unavailable"));
    const state = await h.host.state();
    expect(state.options).toContainEqual(expect.objectContaining({ backend: "claude_subscription", model: "current", active: true }));
  });
});
