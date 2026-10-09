import { readFileSync } from "node:fs";
import ts from "typescript";
import { describe, expect, it, vi } from "vitest";

// extension.ts imports VS Code. Extract and execute its actual composer callback
// with injected host dependencies instead of duplicating its implementation.
const source = readFileSync(new URL("../src/extension.ts", import.meta.url), "utf8");
const tree = ts.createSourceFile("extension.ts", source, ts.ScriptTarget.Latest, true);
let callback: ts.ArrowFunction | undefined;
function visit(node: ts.Node): void {
  if (ts.isArrowFunction(node)
      && node.parameters.map((p) => p.name.getText(tree)).join(",") === "backend,model") {
    callback = node;
  }
  ts.forEachChild(node, visit);
}
visit(tree);
if (!callback) throw new Error("Composer model-swap callback not found");
const js = ts.transpile(`const swap = ${callback.getText(tree)};`, {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None,
});

function setup(catalog: { slug: string; displayName: string; contextWindow?: number | null }[] | undefined) {
  const setProvider = vi.fn(async () => ({ backend: "chatgpt", model: "gpt-b" }));
  const runtimeManager = {
    saveProvider: vi.fn(async () => {}),
    saveContextWindow: vi.fn(async () => {}),
  };
  const credentials = { CRUCIBLE_CHATGPT_REGISTRATION: "registration-1" };
  const providerCredentials = vi.fn(async () => credentials);
  const chatgptCatalog = vi.fn(async () => catalog);
  const state = { refreshed: true };
  const composerModelState = vi.fn(async () => state);
  const swap = new Function("controller", "runtimeManager", "providerCredentials",
    "chatgptCatalog", "composerModelState", `${js}\nreturn swap;`)(
    { configClient: () => ({ setProvider }) }, runtimeManager,
    providerCredentials, chatgptCatalog, composerModelState,
  ) as (backend: string, model: string) => Promise<unknown>;
  return { swap, setProvider, runtimeManager, chatgptCatalog, composerModelState, credentials, state };
}

const catalog = [
  { slug: "gpt-a", displayName: "GPT A", contextWindow: 128000 },
  { slug: "gpt-b", displayName: "GPT B", contextWindow: 200000 },
];

describe("composer model swap context window", () => {
  it("forwards the selected ChatGPT model's window and persists it after a successful swap", async () => {
    const h = setup(catalog);
    expect(await h.swap("chatgpt", "gpt-b")).toEqual(h.state);
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

  it("does not consult the ChatGPT catalog or overwrite the window for other providers", async () => {
    const h = setup(catalog);
    await h.swap("openai", "gpt-b");
    expect(h.chatgptCatalog).not.toHaveBeenCalled();
    expect(h.setProvider).toHaveBeenCalledWith({ backend: "openai", model: "gpt-b", credentials: h.credentials });
    expect(h.runtimeManager.saveContextWindow).not.toHaveBeenCalled();
  });

  it("does not persist anything or refresh the menu if the backend rejects the swap", async () => {
    const h = setup(catalog);
    h.setProvider.mockRejectedValueOnce(new Error("validation failed"));
    await expect(h.swap("chatgpt", "gpt-b")).rejects.toThrow("validation failed");
    expect(h.runtimeManager.saveProvider).not.toHaveBeenCalled();
    expect(h.runtimeManager.saveContextWindow).not.toHaveBeenCalled();
    expect(h.composerModelState).not.toHaveBeenCalled();
  });
});
