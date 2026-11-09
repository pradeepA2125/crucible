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
export async function secureSession(
  session: Session, root: string, voiceAllowed: (contents: WebContents) => boolean = () => false,
): Promise<void> {
  const voiceDocument = (url: string) => isAppURL(url) && new URL(url).pathname === "/desktop-app.html";
  const microphone = (contents: WebContents) => voiceDocument(contents.getURL()) && voiceAllowed(contents);
  session.setPermissionRequestHandler((contents, permission, callback, details) => {
    if (permission === "media") {
      callback("mediaTypes" in details && details.mediaTypes?.length === 1 && details.mediaTypes[0] === "audio" &&
        details.isMainFrame && voiceDocument(details.requestingUrl) && microphone(contents));
      return;
    }
    callback(permission === "clipboard-sanitized-write" && isAppURL(details.requestingUrl) && isAppURL(contents.getURL()));
  });
  session.setPermissionCheckHandler((contents, permission, origin, details) => {
    if (permission === "media") {
      return !!contents && details.isMainFrame && details.mediaType === "audio" && isAppURL(origin) && microphone(contents);
    }
    return permission === "clipboard-sanitized-write" && isAppURL(origin) && !!contents && isAppURL(contents.getURL());
  });
  await session.protocol.handle("app", async request => {
    try {
      if (request.method !== "GET") return new Response(null, { status: 405 });
      const file = await assetPath(root, request.url);
      return new Response(new Uint8Array(await readFile(file)), { headers: {
        "Content-Type": contentType(file), "Content-Security-Policy": ["/graph.html", "/desktop-app.html"].includes(new URL(request.url).pathname) ? CSP.replace("worker-src 'none'", "worker-src 'self'") : CSP,
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
      } });
    } catch { return new Response("Not found", { status: 404, headers: { "Content-Security-Policy": CSP } }); }
  });
}
