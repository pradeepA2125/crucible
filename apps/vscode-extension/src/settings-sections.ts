// vscode-free registry of Settings panel sections. Drives the activity-bar
// TreeView (settings-tree.ts) and the deep-link section argument to the
// crucible.openSettingsPanel command. The ids mirror the webview SectionId union
// (webview-ui/src/settings/sections/meta.ts); test/settings-sections.test.ts is
// the drift guard for that cross-file enum.

export type SettingsSectionId =
  | "overview"
  | "provider"
  | "mcp"
  | "skills"
  | "instructions"
  | "policies"
  | "runtime";

export interface SettingsSection {
  id: SettingsSectionId;
  label: string;
}

/** Nav order = activity-bar tree order = Settings panel nav order. */
export const SETTINGS_SECTIONS: SettingsSection[] = [
  { id: "overview", label: "Overview" },
  { id: "provider", label: "Provider" },
  { id: "mcp", label: "MCP Servers" },
  { id: "skills", label: "Skills" },
  { id: "instructions", label: "Instructions" },
  { id: "policies", label: "Policies & Memory" },
  { id: "runtime", label: "Runtime" },
];

/** Narrow an untyped command argument to a known section id, else undefined.
 * The crucible.openSettingsPanel command is invoked both from the tree (a valid
 * id) and from the command palette (no argument). */
export function asSettingsSectionId(value: unknown): SettingsSectionId | undefined {
  return SETTINGS_SECTIONS.some((s) => s.id === value)
    ? (value as SettingsSectionId)
    : undefined;
}
