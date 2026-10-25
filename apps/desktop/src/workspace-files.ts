import { workspaceRevision, type WorkspaceFileVersion } from "./workspace-revision.js";
import { constants, type BigIntStats } from "node:fs";
import { lstat, open, opendir, realpath, readlink } from "node:fs/promises";
import { join, isAbsolute, resolve, relative, sep, dirname } from "node:path";
import { rasterMediaType, rasterDimensions, MAX_RASTER_BYTES } from "./workspace-raster.js";
import { createHash } from "node:crypto";
import { WORKSPACE_FILE_LIMITS as LIMITS, type WorkspaceDirectory, type WorkspaceEntry, type WorkspaceFileFailure, type WorkspacePreview, type WorkspaceImage } from "./workspace-file-types.js";

export class WorkspaceFileError extends Error {
  constructor(readonly reason: WorkspaceFileFailure, message: string) { super(message); this.name = "WorkspaceFileError"; }
}
const digest = (value: string | Uint8Array) => createHash("sha256").update(value).digest("hex");
/** Largest file compared byte-for-byte against a Git or checkpoint baseline. */
export const COMPARE_BYTES = 8 * 1024 * 1024;
const isProtected = (path: string) => { const parts = path.toLowerCase().split("/"); return parts.includes(".git") || parts.some((part, index) => part === ".crucible" && parts[index + 1] === "state"); };
const sameIdentity = (left: BigIntStats, right: BigIntStats) => left.dev === right.dev && left.ino === right.ino && left.mode === right.mode;
const sameSnapshot = (left: BigIntStats, right: BigIntStats) => sameIdentity(left, right) && left.size === right.size && left.mtimeNs === right.mtimeNs && left.ctimeNs === right.ctimeNs;
export function workspaceRelativePath(value: string): string {
  if (typeof value !== "string" || value.length > LIMITS.pathLength || isAbsolute(value) || /[\\\x00-\x1f\x7f:]/.test(value)) throw new WorkspaceFileError("invalid", "Choose a relative workspace path.");
  if (!value) return "";
  const parts = value.split("/");
  if (parts.length > LIMITS.depth || parts.some(part => !part || part === "." || part === "..")) throw new WorkspaceFileError("invalid", "Choose a relative workspace path without traversal.");
  if (isProtected(value)) throw new WorkspaceFileError("protected", "Internal Git and Crucible data cannot be opened here.");
  return value;
}
function fileError(error: unknown): WorkspaceFileError {
  if (error instanceof WorkspaceFileError) return error;
  const code = (error as NodeJS.ErrnoException)?.code;
  if (code === "ENOENT" || code === "ENOTDIR") return new WorkspaceFileError("missing", "This path is no longer available. Refresh Files.");
  if (code === "ELOOP") return new WorkspaceFileError("symlink", "Links are listed but cannot be followed. Open the actual workspace path.");
  return new WorkspaceFileError("unreadable", "This path cannot be read. Check its permissions and refresh Files.");
}
/** Read-only inspection, not an OS sandbox against a concurrent hostile filesystem writer.
 * Node lacks openat/O_RESOLVE_BENEATH: ancestor swaps retain a narrow TOCTOU window.
 * Identity/ancestor checks fail closed on detected changes. Hard links are ordinary files;
 * a host cannot establish where else the same inode is linked. File Provider reads may stall.
 */
export async function createWorkspaceFiles(selectedRoot: string) {
  const root = await realpath(selectedRoot), rootIdentity = await lstat(root, { bigint: true });
  type CachedEntry = Pick<WorkspaceEntry, "name" | "kind" | "hidden">;
  const cache = new Map<string, { stamp: string; signature: string; entries: CachedEntry[]; limited: boolean; bytes: number }>();
  const stamp = (info: BigIntStats) => [info.dev, info.ino, info.mode, info.mtimeNs, info.ctimeNs].map(String).join(":");
  if (!rootIdentity.isDirectory()) throw new WorkspaceFileError("invalid", "Choose a workspace folder.");
  const inspect = async (requested: string, internalExclude = false) => {
    const path = internalExclude && requested === ".git/info/exclude" ? requested : workspaceRelativePath(requested);
    let absolute = root;
    if (!sameIdentity(rootIdentity, await lstat(root, { bigint: true })) || await realpath(root) !== root) throw new WorkspaceFileError("changed", "The workspace folder changed. Reopen the project.");
    const parts = path ? path.split("/") : [];
    for (const [index, part] of parts.entries()) {
      absolute = join(absolute, part);
      const info = await lstat(absolute, { bigint: true });
      if (info.isSymbolicLink()) throw new WorkspaceFileError("symlink", "Links are listed but cannot be followed. Open the actual workspace path.");
      if (index < parts.length - 1 && !info.isDirectory()) throw new WorkspaceFileError("missing", "The parent folder is unavailable. Refresh Files.");
    }
    if (await realpath(absolute) !== absolute) throw new WorkspaceFileError("changed", "This path changed while opening. Refresh Files.");
    return { path, absolute, info: await lstat(absolute, { bigint: true }) };
  };
  const list = async (requested = "", cursor?: string): Promise<WorkspaceDirectory> => {
    try {
      const target = await inspect(requested);
      if (!target.info.isDirectory()) throw new WorkspaceFileError("invalid", "Choose a workspace directory.");
      const cached = cache.get(target.path);
      let listing = cached?.stamp === stamp(target.info) ? cached : undefined;
      if (!listing) {
        const directory = await opendir(target.absolute), entries: CachedEntry[] = [];
        let limited = false;
        for await (const entry of directory) {
          if (entries.length === LIMITS.directory) { limited = true; break; }
          const path = target.path ? `${target.path}/${entry.name}` : entry.name;
          let kind: WorkspaceEntry["kind"] = isProtected(path) ? "protected" : entry.isSymbolicLink() ? "symlink" : entry.isDirectory() ? "directory" : entry.isFile() ? "file" : "special";
          if (kind === "special" && !entry.isFIFO() && !entry.isSocket() && !entry.isBlockDevice() && !entry.isCharacterDevice()) {
            try { const info = await lstat(join(target.absolute, entry.name)); kind = info.isSymbolicLink() ? "symlink" : info.isDirectory() ? "directory" : info.isFile() ? "file" : "special"; }
            catch { kind = "unavailable"; }
          }
          entries.push({ name: entry.name, kind, hidden: entry.name.startsWith(".") });
        }
        entries.sort((left, right) => Number(right.kind === "directory") - Number(left.kind === "directory") || (left.name < right.name ? -1 : left.name > right.name ? 1 : 0));
        listing = { stamp: stamp(target.info), signature: digest(JSON.stringify([root, target.path, stamp(target.info), entries])), entries, limited,
          bytes: entries.reduce((sum, entry) => sum + Buffer.byteLength(entry.name) * 2 + 64, 0) };
      }
      const { entries: names, signature, limited } = listing;
      let offset = 0;
      if (cursor !== undefined) {
        if (cursor.length > 160 || !/^[A-Za-z0-9_-]+$/.test(cursor)) throw new WorkspaceFileError("invalid", "Invalid Files page. Refresh this folder.");
        let decoded: unknown;
        try { decoded = JSON.parse(Buffer.from(cursor, "base64url").toString("utf8")); } catch { throw new WorkspaceFileError("invalid", "Invalid Files page. Refresh this folder."); }
        const page = decoded as { signature?: unknown; offset?: unknown } | null;
        if (!page || page.signature !== signature) throw new WorkspaceFileError("changed", "The folder changed between pages. Refresh it to continue.");
        if (typeof page.offset !== "number" || !Number.isSafeInteger(page.offset) || page.offset < LIMITS.page || page.offset % LIMITS.page || page.offset >= names.length) throw new WorkspaceFileError("invalid", "Invalid Files page. Refresh this folder.");
        offset = page.offset;
      }
      const entries: WorkspaceEntry[] = names.slice(offset, offset + LIMITS.page).map(entry => ({ ...entry, path: target.path ? `${target.path}/${entry.name}` : entry.name }));
      for (const entry of entries) {
        if (entry.kind === "symlink") {
          try {
            entry.linkTarget = await readlink(join(target.absolute, entry.name));
            // Classify outside targets lexically: probing them can trigger automounts.
            const candidate = resolve(dirname(join(target.absolute, entry.name)), entry.linkTarget);
            const within = relative(root, candidate);
            if (isAbsolute(within) || within === ".." || within.startsWith(`..${sep}`)) entry.targetLocation = "outside";
            else if (isProtected(within.split(sep).join("/"))) entry.targetLocation = "protected";
            else { await inspect(within.split(sep).join("/")); entry.targetLocation = "inside"; entry.targetPath = within.split(sep).join("/"); }
          } catch { entry.targetLocation = "unavailable"; }
        }
        if (entry.kind !== "file") continue;
        try {
          const file = await inspect(entry.path);
          if (!file.info.isFile()) entry.kind = "unavailable"; else entry.size = Number(file.info.size);
        } catch { entry.kind = "unavailable"; }
      }
      if (!sameSnapshot(target.info, (await inspect(target.path)).info)) throw new WorkspaceFileError("changed", "The folder changed while loading. Refresh it to continue.");
      cache.delete(target.path); cache.set(target.path, listing);
      while (cache.size > 8 || [...cache.values()].reduce((sum, value) => sum + value.entries.length, 0) > 20000 || [...cache.values()].reduce((sum, value) => sum + value.bytes, 0) > 8 * 1024 * 1024) cache.delete(cache.keys().next().value!);
      const nextCursor = offset + LIMITS.page < names.length ? Buffer.from(JSON.stringify({ signature, offset: offset + LIMITS.page })).toString("base64url") : undefined;
      return { path: target.path, entries, captured: names.length, limited, ...(nextCursor ? { nextCursor } : {}) };
    } catch (error) { throw fileError(error); }
  };
  const readBytes = async (requested: string, limit: number, internalExclude = false) => {
    try {
      const target = await inspect(requested, internalExclude);
      if (!target.info.isFile()) throw new WorkspaceFileError("special", "Only regular workspace files can be previewed.");
      // Nonblocking + no-follow prevents a replaced file from opening a FIFO or final symlink.
      const handle = await open(target.absolute, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK | (constants.O_NOCTTY ?? 0));
      try {
        const before = await handle.stat({ bigint: true });
        if (!before.isFile() || !sameSnapshot(target.info, before)) throw new WorkspaceFileError("changed", "The file changed while opening. Refresh its preview.");
        if (!sameSnapshot(before, (await inspect(target.path, internalExclude)).info)) throw new WorkspaceFileError("changed", "The path changed before reading. Refresh its preview.");
        const bytes = Buffer.alloc(limit + 1);
        let count = 0;
        while (count < bytes.length) {
          const result = await handle.read(bytes, count, bytes.length - count, count);
          if (!result.bytesRead) break;
          count += result.bytesRead;
        }
        if (!sameSnapshot(before, await handle.stat({ bigint: true })) || !sameSnapshot(before, (await inspect(target.path, internalExclude)).info)) throw new WorkspaceFileError("changed", "The file changed while reading. Refresh its preview.");
        return { path: target.path, content: bytes.subarray(0, Math.min(count, limit)), truncated: count > limit, size: Number(before.size), mode: Number(before.mode), revision: workspaceRevision(target.path, before) };

      } finally { await handle.close(); }
    } catch (error) { throw fileError(error); }
  };
  const readPreview = async (requested: string, internalExclude = false): Promise<WorkspacePreview> => {
    const { path, content, truncated, size, revision } = await readBytes(requested, LIMITS.textBytes, internalExclude);
    if (content.subarray(0, 8192).includes(0)) return { path, kind: "binary", size, revision };
    let text: string;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(content, { stream: truncated }); }
    catch { return { path, kind: "binary", size, revision }; }
    return { path, kind: "text", text, truncated, revision, ...(truncated ? {} : { sha256: digest(content) }), size };
  };
  const image = async (requested: string): Promise<WorkspaceImage> => {
    const { path, content, truncated, size, revision } = await readBytes(requested, MAX_RASTER_BYTES);
    if (truncated) throw new WorkspaceFileError("unsupported", "Image previews are limited to 8 MiB.");
    const mediaType = rasterMediaType(content);
    if (!mediaType) throw new WorkspaceFileError("unsupported", "This file is not a supported PNG, JPEG, GIF, WebP or BMP image.");
    const dimensions = rasterDimensions(content);
    if (!dimensions) throw new WorkspaceFileError("unsupported", "Image dimensions are invalid or exceed the preview limit (16 megapixels, 16,384 pixels per side).");
    return { path, kind: "image", revision, ...dimensions, animated: mediaType === "image/gif" || mediaType === "image/webp" || mediaType === "image/png" && content.includes(Buffer.from("acTL")), mediaType, dataUrl: `data:${mediaType};base64,${content.toString("base64")}`, size, sha256: digest(content) };
  };
  const metadata = async (paths: string[]): Promise<{ entries: WorkspaceFileVersion[] }> => {
    if (paths.length > 12) throw new WorkspaceFileError("invalid", "Inspect at most 12 open file previews.");
    const entries: WorkspaceFileVersion[] = [];
    for (const path of [...new Set(paths)]) {
      try {
        const target = await inspect(path);
        if (!target.info.isFile()) throw new WorkspaceFileError("special", "Only regular workspace files can be inspected.");
        if (!sameSnapshot(target.info, (await inspect(path)).info)) throw new WorkspaceFileError("changed", "The file changed while checking its preview.");
        entries.push({ path, revision: workspaceRevision(path, target.info) });
      } catch (error) { entries.push({ path, reason: fileError(error).reason }); }
    }
    return { entries };
  };
  const ignoreCase = async (): Promise<boolean | undefined> => {
    try {
      const parent = await inspect(""); let scanned = 0;
      for await (const entry of await opendir(root)) {
        if (++scanned > 200) break;
        if (entry.isSymbolicLink() || !/[a-z]/i.test(entry.name)) continue;
        const alternate = entry.name.replace(/[a-z]/i, letter => letter === letter.toLowerCase() ? letter.toUpperCase() : letter.toLowerCase());
        const original = await lstat(join(root, entry.name), { bigint: true });
        let insensitive = false;
        try { insensitive = sameIdentity(original, await lstat(join(root, alternate), { bigint: true })); }
        catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") return undefined; }
        if (!sameSnapshot(parent.info, (await inspect("")).info) || !sameSnapshot(original, await lstat(join(root, entry.name), { bigint: true }))) return undefined;
        return insensitive;
      }
    } catch { return undefined; }
    return undefined;
  };
  /** Host-internal (never IPC): bounded bytes for Git/checkpoint comparison through the same safe read. */
  const compareBytes = (path: string, maxBytes = COMPARE_BYTES) => {
    if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || maxBytes > COMPARE_BYTES) throw new WorkspaceFileError("invalid", "Invalid comparison byte limit.");
    return readBytes(path, maxBytes);
  };
  /** Host-internal: a link's own target text; the link is never followed. */
  const linkText = async (requested: string): Promise<string> => {
    try {
      const path = workspaceRelativePath(requested), parts = path.split("/");
      const parent = await inspect(parts.slice(0, -1).join("/"));
      if (!parent.info.isDirectory()) throw new WorkspaceFileError("missing", "The parent folder is unavailable. Refresh Files.");
      const absolute = join(parent.absolute, parts.at(-1)!);
      if (!(await lstat(absolute)).isSymbolicLink()) throw new WorkspaceFileError("special", "This path is not a link.");
      return await readlink(absolute);
    } catch (error) { throw fileError(error); }
  };
  return { root, list, metadata, preview: (path: string) => readPreview(path), readIgnoreExclude: () => readPreview(".git/info/exclude", true), ignoreCase, image, compareBytes, linkText };
}
