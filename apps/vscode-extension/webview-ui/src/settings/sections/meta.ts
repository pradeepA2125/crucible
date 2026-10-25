import type { IconName } from "../../components/Icon";
import type { SettingsInMsg, SettingsState } from "../types";

export type SectionId =
  | "updates" | "desktop" | "drafts" | "overview" | "appearance" | "provider" | "mcp" | "skills" | "agents" | "instructions" | "policies" | "sandbox" | "runtime";

export interface SectionMeta {
  id: Exclude<SectionId, "overview">;
  label: string;
  icon: IconName;
  blurb: string;
  // Icon accent — a semantic hue per section so the nav reads as a set of
  // distinct destinations rather than an undifferentiated grey list.
  tint: string;
}

/** Nav + Overview registry. Order = nav order = Overview grid order. */
export const SECTIONS: SectionMeta[] = [
  { id: "appearance", label: "Appearance", icon: "orbit", blurb: "Theme and accent colour for every Crucible panel.", tint: "var(--color-accent)" },
  { id: "provider", label: "Provider", icon: "key", blurb: "Model provider, API key, and instant model hot-swap.", tint: "var(--color-code)" },
  { id: "mcp", label: "MCP Servers", icon: "plug", blurb: "External tool servers that extend the agent with new tools.", tint: "var(--color-green)" },
  { id: "skills", label: "Skills", icon: "bolt", blurb: "Workspace skill catalog the agent can load when relevant.", tint: "var(--color-amber)" },
  { id: "agents", label: "Agents", icon: "fork", blurb: "Sub-agents the main agent can dispatch: tools, permissions and trust.", tint: "var(--color-accent-ink)" },
  { id: "instructions", label: "Instructions", icon: "book", blurb: "Project instructions (AGENTS.md) injected into every turn.", tint: "var(--color-accent-ink)" },
  { id: "policies", label: "Policies & Memory", icon: "shield", blurb: "Shell & scope approval policies, memory harness flags.", tint: "var(--color-red)" },
  { id: "sandbox", label: "Sandbox", icon: "term", blurb: "OS limits on what agent commands can write and reach.", tint: "var(--color-green)" },
  { id: "runtime", label: "Runtime", icon: "chip", blurb: "Installed runtime components, versions, and backend restart.", tint: "var(--color-accent)" },
];

/** Desktop-local preferences do not appear in the VS Code settings registry. */
export function sectionsForHost(desktop = false): SectionMeta[] {
  if (!desktop) return SECTIONS;
  return [SECTIONS[0]!, { id: "desktop", label: "Desktop", icon: "chip", blurb: "Background work, notifications and sleep preferences.", tint: "var(--color-accent)" }, {
    id: "drafts", label: "Drafts", icon: "book",
    blurb: "Encrypted storage and recovery for unsent desktop messages.",
    tint: "var(--color-accent)",
  }, { id: "updates", label: "Updates", icon: "retry", blurb: "New desktop versions from the project’s GitHub releases.", tint: "var(--color-accent)" }, ...SECTIONS.slice(1)];
}

/** Icon tint for the Overview destination (not part of SECTIONS). */
export const OVERVIEW_TINT = "var(--color-accent)";

/** Common props every section component receives from the SettingsApp shell. */
export interface SectionProps {
  state: SettingsState;
  busy: boolean;
  send: (msg: SettingsInMsg) => void;
}
