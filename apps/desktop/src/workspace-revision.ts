import { createHash } from "node:crypto";
import type { BigIntStats } from "node:fs";
import type { WorkspaceFileFailure } from "./workspace-file-types.js";
export type WorkspaceFileVersion = { path: string } & ({ revision: string; reason?: never } | { reason: WorkspaceFileFailure; revision?: never });
/** Opaque metadata fingerprint, not a content hash or a filesystem lock. */
export function workspaceRevision(path: string, info: BigIntStats): string {
  return createHash("sha256").update(JSON.stringify([path, info.dev, info.ino, info.mode, info.size, info.mtimeNs, info.ctimeNs].map(String))).digest("hex");
}
