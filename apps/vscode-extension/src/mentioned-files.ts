import * as fs from "node:fs";
import * as path from "node:path";

export const MENTION_FILE_MAX_CHARS = 20_000;

/**
 * Reads @-mentioned files for a chat turn (host pre-reads, per the composer
 * intelligence design — the model sees the content this turn regardless of
 * whether it would have chosen to read_file on its own). A fixed cap, not an
 * env var: this is a UI-side convenience limit, not a backend policy knob.
 */
export function readMentionedFiles(
  workspacePath: string,
  relativePaths: string[]
): { path: string; content: string }[] {
  return relativePaths.map((relativePath) => {
    try {
      const raw = fs.readFileSync(path.join(workspacePath, relativePath), "utf8");
      const content =
        raw.length > MENTION_FILE_MAX_CHARS
          ? `${raw.slice(0, MENTION_FILE_MAX_CHARS)}\n... (truncated)`
          : raw;
      return { path: relativePath, content };
    } catch {
      return { path: relativePath, content: "(file not found or unreadable)" };
    }
  });
}
