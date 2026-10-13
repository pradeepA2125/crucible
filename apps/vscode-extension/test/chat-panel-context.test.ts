import { readFileSync } from "node:fs";
import ts from "typescript";
import { describe, expect, test, vi } from "vitest";
import { ThemeHub, themeReadyReply } from "../src/theme-settings.js";
const text = readFileSync(new URL("../src/chat-panel.ts", import.meta.url), "utf8");
const tree = ts.createSourceFile("chat-panel.ts", text, ts.ScriptTarget.Latest, true);
const declaration = tree.statements.find(n => ts.isClassDeclaration(n) && n.name?.text === "ChatPanel")!;
const js = ts.transpile(declaration.getText(tree), { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS });
// The class source runs without its module scope, so bind the theme helpers it uses
// (theming Phase 1: the message handler answers themeReady before anything else).
const themeHub = new ThemeHub();
const ChatPanel = new Function("exports", "themeReadyReply", "themeHub", `${js}; return ChatPanel;`)({}, themeReadyReply, themeHub);
function setup() {
  const host = Object.create(ChatPanel.prototype) as any;
  let receive!: (message: unknown) => void;
  const postMessage = vi.fn();
  host.panel = { webview: { postMessage, onDidReceiveMessage: (fn: typeof receive) => { receive = fn; } }, onDidDispose: () => {} };
  host.onReady = async () => {}; host.lastWorkbarInfo = null; host.lastContextUsage = null; host.contextUsageStale = false; host.modelRefreshEpoch = 0;
  return { host, postMessage, receive: (m: unknown) => receive(m) };
}
describe("ChatPanel context bridge", () => {
  test("the production controller UI adapter forwards measurements and clears to the panel", () => {
    const source = readFileSync(new URL("../src/extension.ts", import.meta.url), "utf8");
    const ast = ts.createSourceFile("extension.ts", source, ts.ScriptTarget.Latest, true);
    let adapter: ts.Expression | undefined;
    function visit(node: ts.Node): void {
      if (ts.isVariableDeclaration(node) && node.name.getText(ast) === "ui") adapter = node.initializer;
      ts.forEachChild(node, visit);
    }
    visit(ast);
    if (!adapter || !ts.isObjectLiteralExpression(adapter)) throw new Error("Production UI adapter missing");
    const bridge = adapter.properties.find(n => ts.isPropertyAssignment(n) && n.name.getText(ast) === "renderContextUsage");
    if (!bridge || !ts.isPropertyAssignment(bridge)) throw new Error("Context forwarding missing");
    const { host, postMessage } = setup();
    const forward = new Function("chatPanel", ts.transpile(`return (${bridge.initializer.getText(ast)});`, { target: ts.ScriptTarget.ES2022 }))(host);
    const observation = { input_tokens: 52063, context_window: 272000 };
    forward(observation, false);
    forward(null, true);
    expect(postMessage.mock.calls).toEqual([
      [{ type: "renderContextUsage", usage: observation, stale: false }],
      [{ type: "renderContextUsage", usage: null, stale: true }],
    ]);
  });
  test("replays retained observation and stale flag on every webviewReady", () => {
    const { host, postMessage, receive } = setup();
    const observation = { input_tokens: 0, observed_at: "2026-10-08T00:00:00Z" };
    host.renderContextUsage(observation, true); host.registerHandlers(); postMessage.mockClear();
    receive({ type: "webviewReady" }); receive({ type: "webviewReady" });
    expect(postMessage.mock.calls.filter(([m]) => m.type === "renderContextUsage")).toEqual([[{ type: "renderContextUsage", usage: observation, stale: true }], [{ type: "renderContextUsage", usage: observation, stale: true }]]);
  });
  test("older model refresh cannot replace a newer menu or a replacement panel", async () => {
    const { host, postMessage } = setup(); let finish!: (result: unknown) => void; let first = true;
    host.onListModels = async () => { if (first) { first = false; return new Promise(r => { finish = r; }); } return { current: null, options: ["new"] }; };
    const old = host.refreshModels(); await host.refreshModels(); finish({ options: ["old"] }); await old;
    expect(postMessage).toHaveBeenCalledTimes(1); expect(postMessage.mock.calls[0][0].options).toEqual(["new"]);
    host.onListModels = () => new Promise(r => { finish = r; }); const pending = host.refreshModels();
    host.panel = { webview: { postMessage: vi.fn() } }; finish({ options: ["wrong-panel"] }); await pending;
    expect(postMessage).toHaveBeenCalledTimes(1); expect(host.panel.webview.postMessage).not.toHaveBeenCalled();
  });
  test("answers the webview's themeReady with the current theme before any other handling", () => {
    const { host, postMessage, receive } = setup();
    host.registerHandlers();
    themeHub.update({ appearance: "dark", accent: "violet" });
    postMessage.mockClear();
    receive({ type: "themeReady" });
    expect(postMessage).toHaveBeenCalledExactlyOnceWith({ type: "themeChanged", appearance: "dark", accent: "violet" });
  });
});
