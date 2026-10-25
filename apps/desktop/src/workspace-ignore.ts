import ignore, { type Ignore } from "ignore";
import { WorkspaceFileError, workspaceRelativePath, type createWorkspaceFiles } from "./workspace-files.js";
import type { WorkspacePreview } from "./workspace-file-types.js";
type Files = Pick<Awaited<ReturnType<typeof createWorkspaceFiles>>, "preview" | "readIgnoreExclude" | "ignoreCase">;
export type IgnoreStatus = "included" | "ignored" | "unknown";
/** One inspection operation gets fresh local rules; no Git/config execution or global excludes. */
export function createIgnoreWalk(files: Files) {
  const scopes = new Map<string, Promise<Ignore | undefined>>();
  const warnings = new Set<string>();
  let bytes = 0;
  const ignoreCase = files.ignoreCase();
  const readRules = async (read: () => Promise<WorkspacePreview>): Promise<string> => {
    try {
      if (bytes >= 512 * 1024) throw new Error("Ignore rules exceed the inspection limit.");
      const value = await read();
      if (value.kind !== "text" || value.truncated || value.size > 65536 || (bytes += value.size) > 512 * 1024) throw new Error("Ignore rules exceed the inspection limit.");
      return value.text;
    } catch (error) {
      if (error instanceof WorkspaceFileError && error.reason === "missing") return "";
      warnings.add("Some local ignore rules are unavailable or exceed limits; affected results say unknown.");
      throw error;
    }
  };
  const scope = (path: string) => {
    let found = scopes.get(path);
    if (!found) {
      found = (async () => {
        const insensitive = await ignoreCase;
        if (insensitive === undefined) { warnings.add("Ignore case matching could not be detected; results say unknown."); return undefined; }
        const rules = ignore({ ignorecase: insensitive });
        if (!path) rules.add(await readRules(files.readIgnoreExclude));
        rules.add(await readRules(() => files.preview(path ? `${path}/.gitignore` : ".gitignore")));
        return rules;
      })().catch(() => undefined);
      scopes.set(path, found);
    }
    return found;
  };
  const classify = async (requested: string, directory: boolean): Promise<IgnoreStatus> => {
    const path = workspaceRelativePath(requested);
    if (!path) return "included";
    const parts = path.split("/"), active: { base: number; rules: Ignore | undefined }[] = [];
    let unknown = false;
    for (let index = 0; index < parts.length; index++) {
      const rules = await scope(parts.slice(0, index).join("/"));
      active.push({ base: index, rules }); if (!rules) unknown = true;
      let ignored = false;
      for (const entry of active) {
        if (!entry.rules) continue;
        const result = entry.rules.test(parts.slice(entry.base, index + 1).join("/") + (index < parts.length - 1 || directory ? "/" : ""));
        if (result.ignored) ignored = true; else if (result.unignored) ignored = false;
      }
      // Git cannot re-include a child beneath an excluded parent.
      if (ignored) return unknown ? "unknown" : "ignored";
    }
    return unknown ? "unknown" : "included";
  };
  return { classify, warnings };
}
