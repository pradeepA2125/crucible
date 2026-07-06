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

export interface SlashDropdownItem {
  id: string;
  label: string;
  sublabel?: string;
  badge: "Prompt" | "Skill";
}

/**
 * Merge prompt names + skill catalog into one filtered, badged list for the
 * unified "/" dropdown. Prompt-file-wins-on-collision (mirrors resolveSkillCommand):
 * a name present in both lists renders only its Prompt row.
 */
export function buildSlashDropdownItems(
  query: string,
  promptNames: string[],
  skills: { name: string; description: string }[],
): SlashDropdownItem[] {
  const q = query.toLowerCase();
  const promptSet = new Set(promptNames);
  const prompts: SlashDropdownItem[] = promptNames
    .filter((n) => n.toLowerCase().includes(q))
    .map((n) => ({ id: n, label: n, badge: "Prompt" as const }));
  const skillItems: SlashDropdownItem[] = skills
    .filter((s) => !promptSet.has(s.name) && s.name.toLowerCase().includes(q))
    .map((s) => ({ id: s.name, label: s.name, sublabel: s.description, badge: "Skill" as const }));
  return [...prompts, ...skillItems];
}
