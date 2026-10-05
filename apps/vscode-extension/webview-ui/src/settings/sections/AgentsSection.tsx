import { useEffect, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { SectionHeader } from "../SectionHeader";
import type { AgentCatalog, AgentView, SettingsOutMsg } from "../types";
import type { SectionProps } from "./meta";
import { AgentForm, type AgentFormMode } from "./AgentForm";
import { TrustDialog } from "./TrustDialog";

const GROUPS: { source: AgentView["source"]; title: string }[] = [
  { source: "crucible", title: ".crucible/agents" },
  { source: "claude", title: ".claude/agents" },
  { source: "user_claude", title: "~/.claude/agents" },
  { source: "builtin", title: "Built-in" },
];

function permissionLabel(a: AgentView): string {
  return a.permission !== a.declaredPermission
    ? `capped — declared ${a.declaredPermission}`
    : a.permission;
}

function AgentRow({ agent, onOpen, onTrust, onEdit, onDuplicate, onDelete }: {
  agent: AgentView;
  onOpen: () => void;
  onTrust: () => void;
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const [showWarnings, setShowWarnings] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const capped = agent.trust === "capped";
  return (
    <li className="flex flex-col gap-1 border-b py-2 last:border-b-0"
        style={{ borderColor: "var(--hairline)", opacity: agent.active ? 1 : 0.55 }}>
      <div className="flex items-center gap-2">
        {agent.path ? (
          <button type="button" aria-label={`Open ${agent.name}`} onClick={onOpen}
                  className="text-xs font-medium text-text hover:underline">{agent.name}</button>
        ) : (
          <span className="text-xs font-medium text-text">{agent.name}</span>
        )}
        <span className="rounded px-1.5 text-[10px]"
              style={{ background: capped ? "var(--amber-bg)" : "var(--surface-3, transparent)",
                       color: capped ? "var(--color-amber)" : "var(--color-text-3)" }}>
          {permissionLabel(agent)}
        </span>
        {capped && <span className="text-[10px]" style={{ color: "var(--color-amber)" }}>untrusted</span>}
        <span className="text-[10px] text-text-4">{agent.model}</span>
        <span className="text-[10px] text-text-4">
          {agent.tools === null ? "all tools" : `${agent.tools.length} tools`}
        </span>
        {agent.warnings.length > 0 && (
          <button type="button" className="text-[10px]" style={{ color: "var(--color-amber)" }}
                  aria-label={`${agent.warnings.length} warning${agent.warnings.length === 1 ? "" : "s"} on ${agent.name}`}
                  onClick={() => setShowWarnings((v) => !v)}>
            ⚠ {agent.warnings.length}
          </button>
        )}
        <span className="flex-1" />
        {capped && agent.path && agent.source !== "user_claude" && (
          <BtnGhost onClick={onTrust} className="!text-[10px]">
            <span aria-label={`Trust ${agent.name}`}>Trust</span>
          </BtnGhost>
        )}
        {agent.source === "crucible" ? (
          <>
            <BtnGhost onClick={onEdit}><span aria-label={`Edit ${agent.name}`}>Edit</span></BtnGhost>
            {confirming ? (
              <BtnDanger onClick={onDelete}>
                <span aria-label={`Confirm delete ${agent.name}`}>Confirm delete</span>
              </BtnDanger>
            ) : (
              <BtnGhost onClick={() => setConfirming(true)}>
                <span aria-label={`Delete ${agent.name}`}>Delete</span>
              </BtnGhost>
            )}
          </>
        ) : (
          <BtnGhost onClick={onDuplicate}>
            <span aria-label={`Duplicate ${agent.name}`}>Duplicate to .crucible</span>
          </BtnGhost>
        )}
      </div>
      <div className="truncate text-[11px] text-text-3">{agent.description}</div>
      {!agent.path && <div className="text-[10px] text-text-4">built-in — no file</div>}
      {!agent.active && agent.shadowedBy && (
        <div className="text-[10px] text-text-4">overridden by {agent.shadowedBy}</div>
      )}
      {showWarnings && (
        <ul className="ml-3 list-disc text-[10px]" style={{ color: "var(--color-amber)" }}>
          {agent.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}
    </li>
  );
}

/** Settings › Agents (spec §10.2). Loads its own data so a failing catalog route never
 * blanks the rest of the panel; its errors stay inside this section. */
export function AgentsSection({ state, busy, send }: SectionProps) {
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [trusting, setTrusting] = useState<AgentView | null>(null);
  const [form, setForm] = useState<{ mode: AgentFormMode; agent: AgentView | null } | null>(null);

  useEffect(() => {
    const onMessage = (event: MessageEvent<SettingsOutMsg>) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "settings/agents") {
        setCatalog(msg.catalog);
        setError(null);
        setTrusting(null);
        setForm(null);
      } else if (msg.type === "settings/agentsError") {
        setError(msg.message);
      } else if (msg.type === "settings/models") {
        setModels(msg.models);
      }
    };
    window.addEventListener("message", onMessage);
    send({ type: "settings/listAgents" });
    send({ type: "settings/listModels" });
    return () => window.removeEventListener("message", onMessage);
    // send is stable for the panel's lifetime; load once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const crucibleNames = new Set(
    (catalog?.agents ?? []).filter((a) => a.source === "crucible").map((a) => a.name));

  return (
    <div>
      <SectionHeader
        title="Agents"
        description="Definitions the main agent can dispatch. Files in .crucible/agents win over .claude/agents, then ~/.claude/agents, then the built-ins. Workspace files run capped until you trust them."
        action={
          <div className="flex gap-2">
            <BtnGhost onClick={() => send({ type: "settings/listAgents" })}>Refresh</BtnGhost>
            <BtnPrimary disabled={!catalog} onClick={() => setForm({ mode: "new", agent: null })}>
              New agent
            </BtnPrimary>
          </div>
        }
      />
      {error && (
        <div className="anim-slide-down mb-3 rounded-[10px] border px-3 py-2 text-xs"
             style={{ borderColor: "var(--red-brd)", background: "var(--red-bg)", color: "var(--color-red)" }}>
          {error}
        </div>
      )}
      {trusting && (
        <TrustDialog
          agent={trusting}
          onCancel={() => setTrusting(null)}
          onTrust={() => send({ type: "settings/trustAgent", path: trusting.path ?? "",
                                sha256: trusting.sha256 ?? "" })}
        />
      )}
      {form && catalog && (
        <AgentForm
          mode={form.mode}
          initial={form.agent}
          availableTools={catalog.availableTools}
          models={models}
          skillsEnabled={state.skills.length > 0}
          existingNames={crucibleNames}
          busy={busy}
          onCancel={() => setForm(null)}
          onSave={(name, input) => send({ type: "settings/saveAgent", name, input })}
        />
      )}
      {!catalog && !error && <div className="p-4 text-xs text-text-3">Loading agents…</div>}
      {catalog && GROUPS.map(({ source, title }) => {
        const rows = catalog.agents.filter((a) => a.source === source);
        if (rows.length === 0) return null;
        return (
          <div key={source} className="mb-3">
            <CardShell icon="fork" title={title}
                       trailing={<span className="text-[10px] text-text-3">{rows.length}</span>}>
              <ul className="flex flex-col px-3 pb-2 pt-1">
                {rows.map((a) => (
                  <AgentRow
                    key={`${a.source}:${a.path ?? a.name}`}
                    agent={a}
                    onOpen={() => a.path && send({ type: "settings/openFile", path: a.path })}
                    onTrust={() => setTrusting(a)}
                    onEdit={() => setForm({ mode: "edit", agent: a })}
                    onDuplicate={() => setForm({ mode: "duplicate", agent: a })}
                    onDelete={() => send({ type: "settings/deleteAgent", name: a.name })}
                  />
                ))}
              </ul>
            </CardShell>
          </div>
        );
      })}
      {catalog && catalog.skipped.length > 0 && (
        <CardShell icon="warn" title="Skipped files"
                   trailing={<span className="text-[10px] text-text-3">{catalog.skipped.length}</span>}>
          <ul className="flex flex-col px-3 pb-2 pt-1">
            {catalog.skipped.map((s) => (
              <li key={s.path} className="border-b py-1.5 last:border-b-0" style={{ borderColor: "var(--hairline)" }}>
                <div className="font-mono text-[10px] text-text-3">{s.path}</div>
                <div className="text-[11px]" style={{ color: "var(--color-amber)" }}>{s.reason}</div>
              </li>
            ))}
          </ul>
        </CardShell>
      )}
    </div>
  );
}
