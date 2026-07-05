import type { IconName } from "../../components/Icon";
import type { SettingsInMsg, SettingsState } from "../types";

export type SectionId =
  | "overview" | "provider" | "mcp" | "skills" | "instructions" | "policies" | "runtime";

export interface SectionMeta {
  id: Exclude<SectionId, "overview">;
  label: string;
  icon: IconName;
  blurb: string;
}

/** Nav + Overview registry. Order = nav order = Overview grid order. */
export const SECTIONS: SectionMeta[] = [
  { id: "provider", label: "Provider", icon: "key", blurb: "Model provider, API key, and instant model hot-swap." },
  { id: "mcp", label: "MCP Servers", icon: "plug", blurb: "External tool servers that extend the agent with new tools." },
  { id: "skills", label: "Skills", icon: "bolt", blurb: "Workspace skill catalog the agent can load when relevant." },
  { id: "instructions", label: "Instructions", icon: "book", blurb: "Project instructions (AGENTS.md) injected into every turn." },
  { id: "policies", label: "Policies & Memory", icon: "shield", blurb: "Shell & scope approval policies, memory harness flags." },
  { id: "runtime", label: "Runtime", icon: "chip", blurb: "Installed runtime components, versions, and backend restart." },
];

/** Common props every section component receives from the SettingsApp shell. */
export interface SectionProps {
  state: SettingsState;
  busy: boolean;
  send: (msg: SettingsInMsg) => void;
}
