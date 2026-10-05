import type { AgentInput, AgentView } from "../types";
export type AgentFormMode = "new" | "edit" | "duplicate";
export function AgentForm(_props: {
  mode: AgentFormMode; initial: AgentView | null; availableTools: string[]; models: string[];
  skillsEnabled: boolean; existingNames: Set<string>; busy: boolean;
  onCancel: () => void; onSave: (name: string, input: AgentInput) => void;
}) {
  return null;
}
