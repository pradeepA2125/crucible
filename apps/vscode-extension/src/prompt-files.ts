// vscode-free helpers for .crucible/prompts/<name>.md. Pure substitution +
// node-fs reads; no `vscode` import so this stays unit-testable in vitest.
import { promises as fsp } from "fs";
import * as path from "path";

/** Substitute $ARGUMENTS (full string) and $1..$N (whitespace-split positional). */
export function substitutePrompt(body: string, args: string): string {
  const trimmed = args.trim();
  const positional = trimmed.length > 0 ? trimmed.split(/\s+/) : [];
  let out = body.split("$ARGUMENTS").join(trimmed);
  out = out.replace(/\$(\d+)/g, (_match, digits: string) => {
    const idx = Number(digits) - 1;
    return idx >= 0 && idx < positional.length ? (positional[idx] ?? "") : "";
  });
  return out;
}

/** Parse a leading "/name [args]". Returns null when `text` is not a slash command. */
export function parseSlashCommand(text: string): { name: string; args: string } | null {
  const match = /^\/([A-Za-z0-9._-]+)(?:\s+([\s\S]*))?$/.exec(text.trimStart());
  if (!match) return null;
  return { name: match[1] ?? "", args: (match[2] ?? "").trim() };
}

/** Sorted basenames (sans .md) of prompt files; [] on any error. */
export async function listPromptNames(promptsDir: string): Promise<string[]> {
  try {
    const entries = await fsp.readdir(promptsDir);
    return entries
      .filter((e) => e.endsWith(".md"))
      .map((e) => e.slice(0, -3))
      .sort();
  } catch {
    return [];
  }
}

/** Body of <promptsDir>/<name>.md, or null if missing or the name is unsafe. */
export async function loadPromptBody(promptsDir: string, name: string): Promise<string | null> {
  if (!/^[A-Za-z0-9._-]+$/.test(name)) return null;
  try {
    return await fsp.readFile(path.join(promptsDir, `${name}.md`), "utf8");
  } catch {
    return null;
  }
}
