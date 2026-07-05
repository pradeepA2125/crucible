import type { IconName } from "../../components/Icon";
import type { SettingsInMsg, SettingsState } from "../types";

export type SectionId =
  | "overview" | "provider" | "mcp" | "skills" | "instructions" | "policies" | "runtime";

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
  { id: "provider", label: "Provider", icon: "key", blurb: "Model provider, API key, and instant model hot-swap.", tint: "var(--color-code)" },
  { id: "mcp", label: "MCP Servers", icon: "plug", blurb: "External tool servers that extend the agent with new tools.", tint: "var(--color-green)" },
  { id: "skills", label: "Skills", icon: "bolt", blurb: "Workspace skill catalog the agent can load when relevant.", tint: "var(--color-amber)" },
  { id: "instructions", label: "Instructions", icon: "book", blurb: "Project instructions (AGENTS.md) injected into every turn.", tint: "var(--color-accent-ink)" },
  { id: "policies", label: "Policies & Memory", icon: "shield", blurb: "Shell & scope approval policies, memory harness flags.", tint: "var(--color-red)" },
  { id: "runtime", label: "Runtime", icon: "chip", blurb: "Installed runtime components, versions, and backend restart.", tint: "var(--color-accent)" },
];

/** Icon tint for the Overview destination (not part of SECTIONS). */
export const OVERVIEW_TINT = "var(--color-accent)";

/** Common props every section component receives from the SettingsApp shell. */
export interface SectionProps {
  state: SettingsState;
  busy: boolean;
  send: (msg: SettingsInMsg) => void;
}
