import { readFileSync } from "node:fs";
import ts from "typescript";
import { createComposerModelHost } from "../src/composer-model-host.js";
import { describe, expect, it, vi } from "vitest";

const source = readFileSync(new URL("../src/extension.ts", import.meta.url), "utf8");
const tree = ts.createSourceFile("extension.ts", source, ts.ScriptTarget.Latest, true);
type Model = { slug: string; displayName: string; contextWindow: number | null };
function setup() {
  let registration: string | undefined = "reg";
  const models: Model[] = [{ slug: "fresh", displayName: "Fresh", contextWindow: 200000 }];
  const listChatGPTModels = vi.fn(async (_registration: string): Promise<Model[]> => models);
  const refreshContextUsage = vi.fn(async () => {});
  const refreshModels = vi.fn(async () => {});
  const host = createComposerModelHost({
    client: () => ({
      getConfig: async () => ({ provider: null }), listChatGPTModels,
      listClaudeModels: async () => [],
      setProvider: async () => ({ backend: "chatgpt", model: "fresh" }),
    }),
    runtime: {
      chatgptRegistration: () => registration, claudeRegistration: () => undefined,
      getProviderKey: async () => undefined, saveProvider: async () => {},
      saveContextWindow: async () => {}, saveReasoningEffort: async () => {},
    },
    refreshContextUsage, refreshModels,
  });
  return { catalog: host.state, invalidate: () => host.afterSettingsMutation("auth"),
    changed: host.afterSettingsMutation, models, listChatGPTModels, refreshContextUsage, refreshModels,
    setRegistration: (value?: string) => { registration = value; } };
}

function findNode<T extends ts.Node>(root: ts.Node, predicate: (node: ts.Node) => node is T): T {
  let found: T | undefined;
  function walk(node: ts.Node): void {
    if (predicate(node)) found = node;
    ts.forEachChild(node, walk);
  }
  walk(root);
  if (!found) throw new Error("host wiring not found");
  return found;
}

function execute(text: string, bindings: Record<string, unknown>): unknown {
  const code = ts.transpile(text, { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None });
  return new Function(...Object.keys(bindings), code)(...Object.values(bindings));
}

describe("settings mutation wiring", () => {
  it("passes the shared mutation callback to embedded settings", () => {
    const call = findNode(tree, (n): n is ts.CallExpression => ts.isCallExpression(n) && n.expression.getText(tree) === "chatPanel.setSettingsHandlerFactory");
    const callback = vi.fn();
    const createSettingsHandler = vi.fn();
    let factory!: (post: () => void) => unknown;
    const post = vi.fn();
    execute(call.getText(tree), {
      chatPanel: { setSettingsHandlerFactory: (value: typeof factory) => { factory = value; } },
      createSettingsHandler, buildSettingsDeps: () => ({}), runtimeManager: {}, clientFactory: {},
      vscode: { workspace: {} }, settings: {}, afterSettingsMutation: callback,
    });
    factory(post);
    expect(createSettingsHandler).toHaveBeenCalledExactlyOnceWith({}, post, callback);
  });

  it("passes the same callback to standalone settings", () => {
    const call = findNode(tree, (n): n is ts.NewExpression => ts.isNewExpression(n) && n.expression.getText(tree) === "SettingsPanel");
    const callback = vi.fn();
    const panel = vi.fn();
    execute(call.getText(tree), {
      SettingsPanel: panel, context: { extensionUri: "uri" }, runtimeManager: "runtime",
      folder: "folder", clientFactory: "client", settings: {}, afterSettingsMutation: callback,
    });
    expect(panel.mock.calls[0]?.[6]).toBe(callback);
  });

  it("forwards the standalone panel callback to its real handler factory", () => {
    const panelSource = readFileSync(new URL("../src/settings-panel.ts", import.meta.url), "utf8");
    const panelTree = ts.createSourceFile("settings-panel.ts", panelSource, ts.ScriptTarget.Latest, true);
    const declaration = findNode(panelTree, (n): n is ts.ClassDeclaration => ts.isClassDeclaration(n) && n.name?.text === "SettingsPanel");
    const callback = vi.fn();
    const createSettingsHandler = vi.fn(() => vi.fn());
    const panel = { webview: { postMessage: vi.fn(), onDidReceiveMessage: vi.fn() }, onDidDispose: vi.fn() };
    const SettingsPanel = execute(`${declaration.getText(panelTree)}\nreturn SettingsPanel;`, {
      vscode: { window: { createWebviewPanel: () => panel }, ViewColumn: { One: 1 }, Uri: { joinPath: () => ({}) } },
      createSettingsHandler, exports: {},
      // The panel subscribes to theme changes in open() (theming Phase 1).
      themeHub: { settings: { appearance: "vscode", accent: "blue" }, subscribe: () => () => {} },
      injectThemeAttributes: (html: string) => html,
    }) as new (...args: unknown[]) => { open(): void; buildHtml(): string; buildDeps(): unknown };
    const instance = new SettingsPanel({}, {}, "", {}, () => "", async () => {}, callback);
    instance.buildHtml = () => "";
    instance.buildDeps = () => ({});
    instance.open();
    expect(createSettingsHandler.mock.calls[0]?.[2]).toBe(callback);
  });
});

describe("host catalog and auth refresh", () => {
  it("caches ordinary menu polls but invalidates same-registration auth changes", async () => {
    const h = setup();
    await h.catalog();
    await h.catalog();
    expect(h.listChatGPTModels).toHaveBeenCalledTimes(1);
    await h.changed("auth");
    await h.catalog();
    expect(h.listChatGPTModels).toHaveBeenCalledTimes(2);
  });

  it("does not return or cache a late pre-invalidation response", async () => {
    const h = setup();
    let resolve!: (models: Model[]) => void;
    h.listChatGPTModels.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const stale = h.catalog();
    await vi.waitFor(() => expect(h.listChatGPTModels).toHaveBeenCalledTimes(1));
    await h.invalidate();
    expect((await h.catalog()).options).toEqual([expect.objectContaining({ model: "fresh" })]);
    resolve([{ slug: "stale", displayName: "Stale", contextWindow: 128000 }]);
    expect((await stale).options).toEqual([]);
    expect((await h.catalog()).options).toEqual([expect.objectContaining({ model: "fresh" })]);
    expect(h.listChatGPTModels).toHaveBeenCalledTimes(2);
  });

  it("discards an in-flight result after registration changes", async () => {
    const h = setup();
    let resolve!: (models: Model[]) => void;
    h.listChatGPTModels.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const stale = h.catalog();
    await vi.waitFor(() => expect(h.listChatGPTModels).toHaveBeenCalledTimes(1));
    h.setRegistration("other");
    resolve(h.models);
    expect((await stale).options).toEqual([]);
    await h.catalog();
    expect(h.listChatGPTModels).toHaveBeenLastCalledWith("other");
  });

  it.each(["provider", "auth"] as const)("refreshes composer and backend context after %s mutation", async (kind) => {
    const h = setup();
    await h.changed(kind);
    expect(h.refreshContextUsage).toHaveBeenCalledTimes(1);
    expect(h.refreshModels).toHaveBeenCalledTimes(1);
  });
});
