import { realpath, stat } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { APP_ORIGIN } from "./contract.js";

export const DOCUMENTS = new Set(["/index.html", "/setup.html", "/settings.html", "/desktop.html", "/desktop-app.html", "/memory.html", "/graph.html", "/lifecycle.html"]);
export const CSP = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
export function isAppURL(raw: string): boolean {
  try { const url = new URL(raw); return url.protocol === "app:" && url.hostname === "app" && !url.port && !url.username && !url.password; }
  catch { return false; }
}
export function isAppDocument(raw: string): boolean {
  try { return isAppURL(raw) && DOCUMENTS.has(new URL(raw).pathname) && !new URL(raw).search; }
  catch { return false; }
}
export function externalURL(raw: string): string | null {
  try { const url = new URL(raw); return url.protocol === "https:" && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
export function assertSender(sender: { id: number; frameIsMain: boolean; url: string }, expectedId: number): void {
  if (sender.id !== expectedId || !sender.frameIsMain || !isAppDocument(sender.url)) throw new Error("Untrusted desktop sender.");
}
export async function containedPath(root: string, path: string): Promise<string> {
  const canonical = await realpath(resolve(root, path));
  const within = relative(await realpath(root), canonical);
  if (isAbsolute(within) || within === ".." || within.startsWith(`..${sep}`)) throw new Error("File is outside this project.");
  return canonical;
}
export async function assetPath(root: string, raw: string): Promise<string> {
  if (!isAppURL(raw) || /%2f|%5c|%00/i.test(raw)) throw new Error("Invalid asset URL.");
  const url = new URL(raw);
  if (url.search || url.hash) throw new Error("Invalid asset URL.");
  const decoded = decodeURIComponent(url.pathname);
  if (decoded.includes("\\") || decoded.includes("\0") || decoded.split("/").includes("..")) throw new Error("Invalid asset path.");
  const file = await containedPath(root, decoded === "/" ? "index.html" : decoded.slice(1));
  if (!(await stat(file)).isFile()) throw new Error("Asset is not a file.");
  return file;
}
export function contentType(path: string): string {
  const extension = path.split(".").pop();
  return ({ html: "text/html; charset=utf-8", js: "text/javascript; charset=utf-8", css: "text/css; charset=utf-8", svg: "image/svg+xml", png: "image/png", woff2: "font/woff2", json: "application/json" } as Record<string, string>)[extension ?? ""] ?? "application/octet-stream";
}
export const EVENT_TARGET = APP_ORIGIN;
