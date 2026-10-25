import { readFile } from "node:fs/promises";
import type { Session, WebContents } from "electron";
import { assetPath, contentType, CSP, externalURL, isAppURL } from "./security.js";

export const WINDOW_SECURITY = Object.freeze({ contextIsolation: true, sandbox: true,
  nodeIntegration: false, nodeIntegrationInSubFrames: false, nodeIntegrationInWorker: false,
  webSecurity: true, allowRunningInsecureContent: false, webviewTag: false });
export function guardContents(contents: WebContents, openExternal: (url: string) => Promise<unknown>): void {
  contents.on("will-attach-webview", event => event.preventDefault());
  const navigation = (event: Electron.Event, url: string) => { if (!isAppURL(url)) event.preventDefault(); };
  contents.on("will-navigate", navigation);
  contents.on("will-redirect", navigation);
  contents.on("will-frame-navigate", event => { if (!event.isMainFrame || !isAppURL(event.url)) event.preventDefault(); });
  contents.setWindowOpenHandler(({ url }) => {
    const external = externalURL(url);
    if (external) void openExternal(external).catch(() => {});
    return { action: "deny" };
  });
}
export async function secureSession(session: Session, root: string): Promise<void> {
  session.setPermissionRequestHandler((contents, permission, callback, details) => {
    callback(permission === "clipboard-sanitized-write" && isAppURL(details.requestingUrl) && isAppURL(contents.getURL()));
  });
  session.setPermissionCheckHandler((contents, permission, origin) =>
    permission === "clipboard-sanitized-write" && isAppURL(origin) && !!contents && isAppURL(contents.getURL()));
  await session.protocol.handle("app", async request => {
    try {
      if (request.method !== "GET") return new Response(null, { status: 405 });
      const file = await assetPath(root, request.url);
      return new Response(new Uint8Array(await readFile(file)), { headers: {
        "Content-Type": contentType(file), "Content-Security-Policy": new URL(request.url).pathname === "/graph.html" ? CSP.replace("worker-src 'none'", "worker-src 'self'") : CSP,
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
      } });
    } catch { return new Response("Not found", { status: 404, headers: { "Content-Security-Policy": CSP } }); }
  });
}
