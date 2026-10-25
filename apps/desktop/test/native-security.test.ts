import { describe, expect, it, vi } from "vitest";
import { guardContents, secureSession, WINDOW_SECURITY } from "../src/native-security.js";
import { mkdtempSync, writeFileSync, rmSync, realpathSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
describe("native window and session policies", () => {
  it("denies navigation, frames and windows while opening only parsed HTTPS links", () => {
    const handlers = new Map<string, Function>(); const external = vi.fn().mockResolvedValue(undefined); let open!: Function;
    const contents = { on: (name: string, handler: Function) => handlers.set(name, handler), setWindowOpenHandler: (handler: Function) => { open = handler; } };
    guardContents(contents as never, external);
    for (const event of ["will-navigate", "will-redirect"]) { const preventDefault = vi.fn(); handlers.get(event)!({ preventDefault }, "file:///private/file"); expect(preventDefault).toHaveBeenCalled(); }
    const preventDefault = vi.fn(); handlers.get("will-frame-navigate")!({ preventDefault, url: "app://app/index.html", isMainFrame: false }); expect(preventDefault).toHaveBeenCalled();
    expect(open({ url: "javascript:alert(1)" })).toEqual({ action: "deny" }); expect(external).not.toHaveBeenCalled();
    expect(open({ url: "https://example.com/docs" })).toEqual({ action: "deny" }); expect(external).toHaveBeenCalledWith("https://example.com/docs");
    expect(WINDOW_SECURITY).toMatchObject({ sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, webviewTag: false });
  });
  it("serves CSP and no-store headers and rejects traversal with deny-default permissions", async () => {
    const root = realpathSync(mkdtempSync(join(tmpdir(), "crucible-protocol-"))); let handler!: (request: Request) => Promise<Response>; let permission!: Function;
    try {
      writeFileSync(join(root, "index.html"), "<html>fixture</html>");
      await secureSession({ setPermissionRequestHandler: (fn: Function) => { permission = fn; }, setPermissionCheckHandler: vi.fn(), protocol: { handle: (_name: string, fn: typeof handler) => { handler = fn; } } } as never, root);
      const response = await handler(new Request("app://app/index.html")); expect(response.status).toBe(200);
      expect(response.headers.get("content-security-policy")).toContain("connect-src 'none'"); expect(response.headers.get("cache-control")).toBe("no-store");
      expect((await handler(new Request("app://app/%2foutside"))).status).toBe(404);
      const callback = vi.fn(); permission({ getURL: () => "app://app/index.html" }, "clipboard-read", callback, { requestingUrl: "app://app/index.html" }); expect(callback).toHaveBeenCalledWith(false);
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
});

import { parsePayload } from "../src/contract.js";
import { refusalMessages } from "../src/refusal.js";
it("admits only the fieldless read-only receipt action, with no generic draft recovery", () => {
  expect(parsePayload({ type: "checkChatReceipt" })).toEqual({ type: "checkChatReceipt" });
  expect(() => parsePayload({ type: "checkChatReceipt", text: "resend" })).toThrow();
  expect(refusalMessages({ type: "checkChatReceipt" }, "unavailable")).toEqual([]);
});
