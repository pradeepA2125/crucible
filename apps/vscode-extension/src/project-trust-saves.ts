import { createHash } from "node:crypto";
import { readdir, readFile, stat } from "node:fs/promises";
import { join, relative, sep } from "node:path";
import type { TrustKind } from "@crucible/editor-client";

/** Repository files whose trust follows the user's own saves (prompt-injection Phase 1).
 * Mirrors agentd/trust/project_trust.py's KIND_PATHS and fingerprint. */
const KIND_FILES: Record<string, TrustKind> = {
  "AGENTS.md": "agents_md",
  ".crucible/mcp.json": "mcp",
  ".crucible/approved-commands.json": "approved_commands",
  ".crucible/approved-mcp-tools.json": "approved_mcp_tools",
  ".crucible/approved-web-fetch.json": "approved_web_fetch",
  ".crucible/approved-web-search.json": "approved_web_search",
};

export function trustKindForPath(workspace: string, path: string): TrustKind | null {
  const rel = relative(workspace, path).split(sep).join("/");
  if (rel.startsWith("../") || rel === "..") return null;
  const parts = rel.split("/");
  if (parts.length === 4 && parts[0] === ".crucible" && parts[1] === "skills" && parts[3] === "SKILL.md") {
    return "skills";
  }
  return KIND_FILES[rel] ?? null;
}

const sha256 = (data: Buffer | string): string => createHash("sha256").update(data).digest("hex");

/** Thrown when a covered file exists but can't be read: never reported as "was absent". */
class UnreadableFile extends Error {}

async function fileSha(path: string): Promise<string | null> {
  try {
    if (!(await stat(path)).isFile()) return null;
    return sha256(await readFile(path));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
    // A null before-hash means "the user created it", which the backend trusts outright.
    throw new UnreadableFile(path);
  }
}

/** The whole skill set's fingerprint: sha256 of "rel\0sha" lines over sorted SKILL.md files. */
async function skillsFingerprint(workspace: string): Promise<string | null> {
  const root = join(workspace, ".crucible", "skills");
  let names: string[];
  try {
    names = await readdir(root);
  } catch {
    return null;
  }
  const pairs: [string, string][] = [];
  for (const name of names) {
    const sha = await fileSha(join(root, name, "SKILL.md"));
    if (sha !== null) pairs.push([`.crucible/skills/${name}/SKILL.md`, sha]);
  }
  if (pairs.length === 0) return null;
  pairs.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return sha256(pairs.map(([rel, sha]) => `${rel}\0${sha}`).join("\n"));
}

export async function trustFingerprint(workspace: string, kind: TrustKind, path: string): Promise<string | null> {
  return kind === "skills" ? skillsFingerprint(workspace) : fileSha(path);
}

/** The user saving a covered file in the editor is their own change: the backend trusts the
 * result only when what it replaced was trusted or absent. `willSave` runs before the bytes
 * reach disk, so it records what the file was. */
export class ProjectTrustSaves {
  private readonly before = new Map<string, string | null>();

  constructor(
    private readonly workspace: string,
    private readonly report: (kind: TrustKind, before: string | null) => Promise<boolean>,
    private readonly refresh: () => Promise<void>,
  ) {}

  async willSave(path: string): Promise<void> {
    const kind = trustKindForPath(this.workspace, path);
    if (kind === null) return;
    try {
      this.before.set(path, await trustFingerprint(this.workspace, kind, path));
    } catch (err) {
      if (!(err instanceof UnreadableFile)) throw err;
      this.before.delete(path); // nothing to report: the file stays as trusted as it was
    }
  }

  async didSave(path: string): Promise<void> {
    const kind = trustKindForPath(this.workspace, path);
    if (kind === null || !this.before.has(path)) return;
    const before = this.before.get(path) ?? null;
    this.before.delete(path);
    await this.report(kind, before);
    await this.refresh();
  }
}
