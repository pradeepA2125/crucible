import { useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { Switch } from "../../components/shared/Switch";
import { SectionHeader } from "../SectionHeader";
import type { SectionProps } from "./meta";

/** SkillsSection — searchable list of discovered skills with enable switches. */
export function SkillsSection({ state, send }: SectionProps) {
  const [filter, setFilter] = useState("");
  const q = filter.trim().toLowerCase();
  const skills = state.skills.filter(
    (s) => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q),
  );

  return (
    <div>
      <SectionHeader
        title="Skills"
        description="SKILL.md folders discovered in .ai-editor/skills. Disabling a skill hides it from the agent (requires a backend restart)."
        search={{ value: filter, onChange: setFilter }}
      />
      <CardShell
        icon="bolt"
        title="Workspace skills"
        trailing={<span className="text-[10px] text-text-3">{state.skills.length}</span>}
      >
        <ul className="flex flex-col px-3 pb-2 pt-1">
          {skills.map((s, i) => (
            <li
              key={s.name}
              className="anim-section flex items-center gap-2.5 border-b py-2 last:border-b-0"
              style={{ borderColor: "var(--hairline)", animationDelay: `${i * 25}ms` }}
            >
              <Switch
                checked={s.enabled}
                label={`Enable ${s.name}`}
                onChange={(next) => send({ type: "settings/skillToggle", name: s.name, enabled: next })}
              />
              <span className="text-xs font-medium text-text">{s.name}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-text-3">{s.description}</span>
            </li>
          ))}
          {skills.length === 0 && (
            <li className="py-5 text-center text-[11px] text-text-3">
              {state.skills.length === 0 ? "No skills discovered in this workspace." : "No skills match the search."}
            </li>
          )}
        </ul>
      </CardShell>
    </div>
  );
}
