// Local mirror of the host's parseSlashCommand (src/prompt-files.ts). The webview
// is a separate Vite bundle that doesn't import the extension's src/, so this small
// dependency-free helper is mirrored here (like the webview's mirror types).

/** Parse a leading "/name [args]". Returns null when `text` is not a slash command. */
export function parseSlashCommand(text: string): { name: string; args: string } | null {
  const match = /^\/([A-Za-z0-9._-]+)(?:\s+([\s\S]*))?$/.exec(text.trimStart());
  if (!match) return null;
  return { name: match[1] ?? "", args: (match[2] ?? "").trim() };
}

/**
 * Resolve a `/name args` against the known skill catalog for deterministic forced-load.
 * Returns the message to send + the forced skill, or null when `name` is not a skill.
 * Called only AFTER prompt-file expansion missed (found=false), so a prompt file of the
 * same name always wins (the host resolves prompts first).
 */
export function resolveSkillCommand(
  name: string,
  args: string,
  skillNames: string[],
): { forcedSkills: string[]; message: string } | null {
  if (!skillNames.includes(name)) return null;
  return { forcedSkills: [name], message: args };
}
