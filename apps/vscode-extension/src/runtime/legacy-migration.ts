import { existsSync, renameSync } from "node:fs";
import { join } from "node:path";

/**
 * One-time move of the managed runtime root ~/.ai-editor -> ~/.crucible.
 * Best-effort: on any failure the new path is returned anyway and the
 * installer treats it as a fresh install.
 */
export function migrateLegacyRuntimeRoot(homeDir: string): string {
  const legacy = join(homeDir, ".ai-editor");
  const target = join(homeDir, ".crucible");
  if (existsSync(join(legacy, "runtime")) && !existsSync(target)) {
    try {
      renameSync(legacy, target);
    } catch {
      // fresh-install fallback — installer recreates everything under target
    }
  }
  return join(target, "runtime");
}
