import type { BackendConfig } from "@crucible/editor-client";
import { createComposerModelHost, type ComposerModelPorts } from "../src/composer-model-host.js";
import { describe, expect, it, vi } from "vitest";

// Exercise the exported coordinator, including its actual catalog/state path.
type Client = ReturnType<ComposerModelPorts["client"]>;
function setup(catalog: { slug: string; displayName: string; contextWindow?: number | null }[] | undefined) {
  let provider: NonNullable<BackendConfig["provider"]> = { backend: "chatgpt", model: "gpt-b", usesChatgptPlan: true };
  const setProvider = vi.fn<Client["setProvider"]>(async request => {
    provider = { ...provider, backend: request.backend, model: request.model ?? provider.model };
    return { backend: provider.backend, model: provider.model };
  });
  const getConfig = vi.fn(async () => ({ provider }));
  const listChatGPTModels = vi.fn<Client["listChatGPTModels"]>(async () => (catalog ?? []).map(model => ({ ...model, contextWindow: model.contextWindow ?? null })));
  const refreshContextUsage = vi.fn(async () => {});
  const runtimeManager = {
    chatgptRegistration: () => "registration-1", claudeRegistration: () => undefined,
    getProviderKey: async () => undefined,
    saveProvider: vi.fn(async () => {}), saveContextWindow: vi.fn(async () => {}),
    saveReasoningEffort: vi.fn(async () => {}),
  } satisfies ComposerModelPorts["runtime"];
  const host = createComposerModelHost({
    client: () => ({ getConfig, setProvider, listChatGPTModels, listClaudeModels: async () => [] }),
    runtime: runtimeManager, refreshContextUsage, refreshModels: async () => {},
  });
  const credentials = { CRUCIBLE_CHATGPT_REGISTRATION: "registration-1" };
  return { swap: host.setModel, setProvider, refreshContextUsage, runtimeManager, getConfig, credentials };
}

const catalog = [
  { slug: "gpt-a", displayName: "GPT A", contextWindow: 128000 },
  { slug: "gpt-b", displayName: "GPT B", contextWindow: 200000 },
];

describe("composer model swap context window", () => {
  it.each(["chatgpt", "openai"])("hydrates backend context after accepted %s swap", async (backend) => {
    const h = setup(catalog);
    await h.swap(backend, "gpt-b");
    expect(h.refreshContextUsage).toHaveBeenCalledTimes(1);
    expect(h.setProvider.mock.invocationCallOrder[0]).toBeLessThan(h.refreshContextUsage.mock.invocationCallOrder[0]!);
    expect(h.getConfig).toHaveBeenCalledTimes(1);
  });
  it("forwards the selected ChatGPT model's window and persists it after a successful swap", async () => {
    const h = setup(catalog);
    expect((await h.swap("chatgpt", "gpt-b")).current).toMatchObject({ backend: "chatgpt", model: "gpt-b" });
    expect(h.setProvider).toHaveBeenCalledWith({
      backend: "chatgpt", model: "gpt-b", credentials: h.credentials, contextWindow: 200000,
    });
    expect(h.runtimeManager.saveProvider).toHaveBeenCalledWith("chatgpt", "gpt-b");
    expect(h.runtimeManager.saveContextWindow).toHaveBeenCalledWith(200000);
    expect(h.setProvider.mock.invocationCallOrder[0]).toBeLessThan(
      h.runtimeManager.saveContextWindow.mock.invocationCallOrder[0]!);
  });

  it("updates the window when switching from a larger to a smaller model", async () => {
    const h = setup(catalog);
    await h.swap("chatgpt", "gpt-b");
    await h.swap("chatgpt", "gpt-a");
    expect(h.setProvider).toHaveBeenLastCalledWith({
      backend: "chatgpt", model: "gpt-a", credentials: h.credentials, contextWindow: 128000,
    });
    expect(h.runtimeManager.saveContextWindow).toHaveBeenLastCalledWith(128000);
  });

  it.each([undefined, [], [{ slug: "other", displayName: "Other", contextWindow: 128000 }]])(
    "leaves the window unchanged when the catalog has no selected model: %j", async (models) => {
      const h = setup(models);
      await h.swap("chatgpt", "gpt-b");
      expect(h.setProvider).toHaveBeenCalledWith({ backend: "chatgpt", model: "gpt-b", credentials: h.credentials });
      expect(h.runtimeManager.saveContextWindow).not.toHaveBeenCalled();
    });

  it.each([null, undefined, 0, -1, NaN, Infinity, 1.5, 1, 1023, 10_000_001])(
    "ignores an unavailable or invalid catalog window: %s", async (contextWindow) => {
      const h = setup([{ slug: "gpt-b", displayName: "GPT B", contextWindow }]);
      await h.swap("chatgpt", "gpt-b");
      expect(h.setProvider).toHaveBeenCalledWith({ backend: "chatgpt", model: "gpt-b", credentials: h.credentials });
      expect(h.runtimeManager.saveContextWindow).not.toHaveBeenCalled();
    });

  it.each([1024, 10_000_000])("accepts a supported boundary window: %s", async (contextWindow) => {
    const h = setup([{ slug: "gpt-b", displayName: "GPT B", contextWindow }]);
    await h.swap("chatgpt", "gpt-b");
    expect(h.setProvider).toHaveBeenCalledWith({
      backend: "chatgpt", model: "gpt-b", credentials: h.credentials, contextWindow,
    });
    expect(h.runtimeManager.saveContextWindow).toHaveBeenCalledWith(contextWindow);
  });

  it("does not apply the ChatGPT catalog window to another provider", async () => {
    const h = setup(catalog);
    await h.swap("openai", "gpt-b");
    expect(h.setProvider).toHaveBeenCalledWith({ backend: "openai", model: "gpt-b" });
    expect(h.runtimeManager.saveContextWindow).not.toHaveBeenCalled();
  });

  it("does not persist anything or refresh the menu if the backend rejects the swap", async () => {
    const h = setup(catalog);
    h.setProvider.mockRejectedValueOnce(new Error("validation failed"));
    await expect(h.swap("chatgpt", "gpt-b")).rejects.toThrow("validation failed");
    expect(h.runtimeManager.saveProvider).not.toHaveBeenCalled();
    expect(h.runtimeManager.saveContextWindow).not.toHaveBeenCalled();
    expect(h.getConfig).not.toHaveBeenCalled();
    expect(h.refreshContextUsage).not.toHaveBeenCalled();
  });
});
