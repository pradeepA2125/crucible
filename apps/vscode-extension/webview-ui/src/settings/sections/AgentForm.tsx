import { useState } from "react";
import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { FIELD } from "../ui";
import type { AgentInput, AgentView } from "../types";

export type AgentFormMode = "new" | "edit" | "duplicate";

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const PERMISSIONS = ["default", "acceptEdits", "plan", "dontAsk"];

function ToolChecks({ label, tools, checked, onToggle }: {
  label: string; tools: string[]; checked: Set<string>; onToggle: (t: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1">
      {tools.map((t) => (
        <label key={t} className="flex items-center gap-1 text-[11px] text-text-2">
          <input type="checkbox" aria-label={`${label} ${t}`} checked={checked.has(t)}
                 onChange={() => onToggle(t)} />
          <span className="font-mono">{t}</span>
        </label>
      ))}
    </div>
  );
}

/** New / Edit / Duplicate form for a .crucible/agents definition (spec §10.2). */
export function AgentForm({ mode, initial, availableTools, models, skillsEnabled, existingNames, busy,
                            onCancel, onSave }: {
  mode: AgentFormMode; initial: AgentView | null; availableTools: string[]; models: string[];
  skillsEnabled: boolean; existingNames: Set<string>; busy: boolean;
  onCancel: () => void; onSave: (name: string, input: AgentInput) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [persona, setPersona] = useState(initial?.persona ?? "");
  const [allTools, setAllTools] = useState(initial ? initial.tools === null : true);
  const [tools, setTools] = useState(new Set(initial?.tools ?? []));
  const [disallowed, setDisallowed] = useState(new Set(initial?.disallowedTools ?? []));
  const [permission, setPermission] = useState(initial?.declaredPermission ?? "default");
  const [model, setModel] = useState(initial?.model ?? "inherit");
  const [maxTurns, setMaxTurns] = useState(initial?.maxTurns != null ? String(initial.maxTurns) : "");
  const [skills, setSkills] = useState((initial?.skills ?? []).join(", "));

  const toolOptions = [...new Set([...availableTools, ...(initial?.tools ?? []),
                                    ...(initial?.disallowedTools ?? [])])];
  const modelOptions = [...new Set(["inherit", ...models, ...(initial ? [initial.model] : [])])];
  const turns = maxTurns.trim() === "" ? null : Number(maxTurns);
  const turnsOk = turns === null || (Number.isInteger(turns) && turns >= 1 && turns <= 200);
  const nameOk = NAME_RE.test(name) && name !== "trust";
  const valid = nameOk && description.trim() !== "" && turnsOk;
  const replaces = mode !== "edit" && existingNames.has(name);
  const toggle = (set: Set<string>, update: (s: Set<string>) => void, t: string) => {
    const next = new Set(set);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    update(next);
  };

  const save = () => {
    const input: AgentInput = {
      description: description.trim(),
      persona,
      tools: allTools ? null : toolOptions.filter((t) => tools.has(t)),
      disallowedTools: toolOptions.filter((t) => disallowed.has(t)),
      permission,
      model,
      maxTurns: turns,
      skills: skills.split(",").map((s) => s.trim()).filter((s) => s),
    };
    if (mode === "edit" && initial && initial.name !== name) input.renameFrom = initial.name;
    onSave(name, input);
  };

  const title = mode === "new" ? "New agent" : mode === "edit" ? `Edit ${initial?.name ?? ""}`
    : `Duplicate ${initial?.name ?? ""} to .crucible/agents`;

  return (
    <div className="surface-card anim-slide-down mb-3 flex flex-col gap-2.5 p-3" role="form" aria-label={title}>
      <div className="text-xs font-medium text-text">{title}</div>
      <label className="flex flex-col gap-1 text-[11px] text-text-3">
        Name
        <input aria-label="Name" className={FIELD} value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {!nameOk && name !== "" && (
        <div className="text-[10px]" style={{ color: "var(--color-red)" }}>
          1–64 characters of letters, digits, _ . - starting with a letter or digit; “trust” is reserved.
        </div>
      )}
      {replaces && (
        <div className="text-[10px]" style={{ color: "var(--color-amber)" }}>
          Saving replaces the existing .crucible/agents/{name}.md
        </div>
      )}
      <label className="flex flex-col gap-1 text-[11px] text-text-3">
        Description
        <input aria-label="Description" className={FIELD} value={description}
               onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-text-3">
        Role / persona
        <textarea aria-label="Role / persona" className={`${FIELD} min-h-[96px] font-mono`}
                  value={persona} onChange={(e) => setPersona(e.target.value)} />
      </label>
      <div className="flex flex-col gap-1 text-[11px] text-text-3">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" aria-label="All tools" checked={allTools}
                 onChange={() => setAllTools((v) => !v)} />
          All tools
        </label>
        {!allTools && (
          <ToolChecks label="Allow" tools={toolOptions} checked={tools}
                      onToggle={(t) => toggle(tools, setTools, t)} />
        )}
      </div>
      <div className="flex flex-col gap-1 text-[11px] text-text-3">
        Disallowed tools
        <ToolChecks label="Disallow" tools={toolOptions} checked={disallowed}
                    onToggle={(t) => toggle(disallowed, setDisallowed, t)} />
      </div>
      <div className="flex gap-3">
        <label className="flex flex-col gap-1 text-[11px] text-text-3">
          Permission
          <select aria-label="Permission" className={FIELD} value={permission}
                  onChange={(e) => setPermission(e.target.value)}>
            {PERMISSIONS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-text-3">
          Model
          <select aria-label="Model" className={FIELD} value={model} onChange={(e) => setModel(e.target.value)}>
            {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-text-3">
          Max turns
          <input aria-label="Max turns" className={`${FIELD} w-20`} inputMode="numeric"
                 value={maxTurns} onChange={(e) => setMaxTurns(e.target.value)} />
        </label>
      </div>
      {(skillsEnabled || (initial?.skills.length ?? 0) > 0) && (
        <label className="flex flex-col gap-1 text-[11px] text-text-3">
          Skills (comma-separated)
          <input aria-label="Skills" className={FIELD} value={skills} onChange={(e) => setSkills(e.target.value)} />
        </label>
      )}
      <div className="flex justify-end gap-2">
        <BtnGhost onClick={onCancel}>Cancel</BtnGhost>
        <BtnPrimary disabled={!valid || busy} onClick={save}>Save agent</BtnPrimary>
      </div>
    </div>
  );
}
