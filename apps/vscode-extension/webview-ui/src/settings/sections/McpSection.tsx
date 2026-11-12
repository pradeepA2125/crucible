import { useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { Switch } from "../../components/shared/Switch";
import { SectionHeader } from "../SectionHeader";
import { buildMcpEntry, type McpTransport } from "../mcpEntry";
import { nextToolFilters } from "../mcpToolFilters";
import type { McpServerRow } from "../types";
import { FIELD } from "../ui";
import type { SectionProps } from "./meta";
import { ProjectTrustDialog } from "../../components/trust/ProjectTrustDialog";
import { untrustedFiles, useProjectTrust } from "../../components/trust/projectTrust";
import type { TrustChannel, TrustFile } from "../../components/trust/projectTrust";
import { vscode } from "../vscodeApi";

/** Settings' trust channel: the dialog posts through the settings host, not the chat's. */
const SETTINGS_TRUST_CHANNEL: TrustChannel = {
  read: () => vscode.postMessage({ type: "settings/projectTrust" }),
  set: (kind, sha256) => vscode.postMessage({ type: "settings/trustProjectFile", kind, sha256 }),
  parse: message => {
    const m = message as { type?: unknown; files?: unknown; error?: unknown } | null;
    if (m?.type !== "settings/projectTrustState" || !Array.isArray(m.files)) return null;
    return { files: m.files as TrustFile[], ...(typeof m.error === "string" ? { error: m.error } : {}) };
  },
};

/** The review dialog, mounted only while open so it reads trust fresh each time. */
function TrustReview({ onClose }: { onClose(): void }) {
  const { view, trust } = useProjectTrust(SETTINGS_TRUST_CHANNEL);
  if (!view) return null;
  return <ProjectTrustDialog files={untrustedFiles(view.files)} error={view.error}
    onTrust={trust} onClose={onClose} />;
}

/** Colored status dot; the transient "connecting" state pulses a ring. */
function StatusDot({ state }: { state: string }) {
  const color =
    state === "connected" ? "var(--color-green)"
    : state === "connecting" || state === "untrusted" ? "var(--color-amber)"
    : state === "failed" ? "var(--color-red)"
    : "var(--color-text-4)";
  return (
    <span
      aria-label={state}
      className="inline-block h-[7px] w-[7px] flex-shrink-0 rounded-full"
      style={{
        background: color,
        animation: state === "connecting" ? "dot-pulse 1.2s ease-in-out infinite" : undefined,
      }}
    />
  );
}

/** Collapsed list of every tool a connected server offers, one checkbox each. Servers
 *  can list dozens of tools, so it stays closed until asked for. */
function ToolList({ server, busy, send }: { server: McpServerRow } & Pick<SectionProps, "busy" | "send">) {
  const [open, setOpen] = useState(false);
  if (server.tools.length === 0) return null;
  const enabled = server.tools.filter((t) => t.enabled).length;
  return (
    <div className="px-3 pb-2">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`Tools for ${server.name}: ${enabled} of ${server.tools.length} enabled`}
        className="text-[11px] text-text-3 hover:text-text-1"
        onClick={() => setOpen(!open)}
      >
        {open ? "▾" : "▸"} Tools · {enabled} of {server.tools.length} enabled
      </button>
      {open && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {server.tools.map((tool) => (
            <li key={tool.name}>
              <label className="flex items-start gap-2 text-[11px]" title={tool.description}>
                <input
                  type="checkbox"
                  checked={tool.enabled}
                  disabled={busy}
                  onChange={(e) => send({
                    type: "settings/mcpTools",
                    name: server.name,
                    ...nextToolFilters(server, tool.name, e.target.checked),
                  })}
                />
                <span className="font-mono text-text-2">{tool.name}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const SOURCE_LABEL = { saved: "Saved", environment: "From environment" } as const;

/** One row per ${VAR} the server's config uses: where its value comes from, a password field
 *  that never shows a stored value, Save, and Clear for a saved one. Values live in the
 *  editor's secret storage (the desktop keychain), never in mcp.json. */
function SecretList({ server, busy, send }: { server: McpServerRow } & Pick<SectionProps, "busy" | "send">) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const secrets = server.secrets ?? [];
  if (secrets.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1.5 px-3 pb-2" aria-label={`Secrets for ${server.name}`}>
      {secrets.map(({ name, source }) => {
        const draft = drafts[name] ?? "";
        const save = () => {
          if (!draft.trim()) return;
          send({ type: "settings/mcpSecrets", values: { [name]: draft } });
          setDrafts((current) => ({ ...current, [name]: "" }));
        };
        return (
          <li key={name} className="flex min-w-0 flex-wrap items-center gap-2 text-[11px]">
            <span className="font-mono text-text-2">{name}</span>
            <span style={{ color: source ? "var(--color-text-3)" : "var(--color-amber)" }}>
              {source ? SOURCE_LABEL[source] : "Not set"}
            </span>
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              aria-label={`Value for ${name} on ${server.name}`}
              className={`${FIELD} min-w-[10rem] flex-1`}
              placeholder={source === "saved" ? "Replace saved value" : "Enter value"}
              value={draft}
              onChange={(e) => setDrafts((current) => ({ ...current, [name]: e.target.value }))}
              onKeyDown={(e) => { if (e.key === "Enter") save(); }}
            />
            <BtnPrimary disabled={busy || !draft.trim()} aria-label={`Save ${name}`} onClick={save}>Save</BtnPrimary>
            {source === "saved" && (
              <BtnGhost disabled={busy} aria-label={`Clear ${name}`}
                onClick={() => send({ type: "settings/mcpSecrets", values: { [name]: null } })}>
                Clear
              </BtnGhost>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** McpSection — server list (status dot, tool count, switch, reconnect/remove) + add form. */
export function McpSection({ state, busy, send }: SectionProps) {
  const [filter, setFilter] = useState("");
  const [transport, setTransport] = useState<McpTransport>("stdio");
  const [name, setName] = useState("");
  const [commandOrUrl, setCommandOrUrl] = useState("");
  const [envVars, setEnvVars] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const untrusted = state.mcp.trusted === false;

  const servers = state.mcp.servers.filter((s) =>
    s.name.toLowerCase().includes(filter.trim().toLowerCase()),
  );

  return (
    <div>
      <SectionHeader
        title="MCP Servers"
        description="External tool servers from .crucible/mcp.json. Every tool call is approval-gated in chat."
        search={{ value: filter, onChange: setFilter }}
      />

      {untrusted && (
        <div role="status" className="surface-card mb-3 flex min-w-0 flex-wrap items-center gap-2 px-3 py-2 text-[11px]">
          <span className="min-w-0 flex-1 text-text-2">
            This project's mcp.json isn't trusted yet, so none of its servers start. Review it to connect them.
          </span>
          <BtnPrimary onClick={() => setReviewing(true)}>Review</BtnPrimary>
        </div>
      )}
      {reviewing && <TrustReview onClose={() => setReviewing(false)} />}

      {!state.mcp.enabled && (
        <p className="mb-3 text-[11px]" style={{ color: "var(--color-amber)" }}>
          MCP is disabled (CRUCIBLE_MCP_ENABLED=0) — servers below stay dormant.
        </p>
      )}

      <div className="flex flex-col gap-2.5">
        {servers.map((s, i) => (
          <div key={s.name} className="anim-section" style={{ animationDelay: `${i * 35}ms` }}>
            <CardShell
              icon="plug"
              title={s.name}
              subtitle={s.state === "untrusted" ? `${s.transport} · Waiting for trust`
                : `${s.transport} · ${s.toolCount} tools`}
              badge={<StatusDot state={s.state} />}
              trailing={
                <span className="flex items-center gap-1.5">
                  <Switch
                    checked={s.userEnabled}
                    label={`Enable ${s.name}`}
                    onChange={(next) => send({ type: "settings/mcpToggle", name: s.name, enabled: next })}
                  />
                  <BtnGhost disabled={busy} onClick={() => send({ type: "settings/mcpReconnect", name: s.name })}>
                    Reconnect
                  </BtnGhost>
                  <BtnDanger disabled={busy} onClick={() => send({ type: "settings/mcpDelete", name: s.name })}>
                    Remove
                  </BtnDanger>
                </span>
              }
            >
              {s.detail && <p className="px-3 pb-2 text-[11px] text-text-3">{s.detail}</p>}
              <SecretList server={s} busy={busy} send={send} />
              <ToolList server={s} busy={busy} send={send} />
            </CardShell>
          </div>
        ))}
        {servers.length === 0 && (
          <p className="py-6 text-center text-[11px] text-text-3">
            {state.mcp.servers.length === 0 ? "No MCP servers configured." : "No servers match the search."}
          </p>
        )}
      </div>

      <div className="mt-4">
        <CardShell icon="plus" title="Add server">
          <div className="flex flex-col gap-2 px-3 pb-3 pt-1">
            <div className="flex gap-2">
              <input className={`${FIELD} w-40`} placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
              <select className={FIELD} value={transport} onChange={(e) => setTransport(e.target.value as McpTransport)}>
                <option value="stdio">stdio</option>
                <option value="http">http</option>
                <option value="sse">sse</option>
              </select>
            </div>
            <input
              className={FIELD}
              placeholder={transport === "stdio" ? "command line (e.g. uv run server.py)" : "url"}
              value={commandOrUrl}
              onChange={(e) => setCommandOrUrl(e.target.value)}
            />
            <input
              className={FIELD}
              placeholder="secret names, comma-separated (e.g. GITHUB_PAT)"
              value={envVars}
              onChange={(e) => setEnvVars(e.target.value)}
            />
            <BtnPrimary
              className="self-start"
              disabled={busy || !name || !commandOrUrl}
              onClick={() => {
                const envVarNames = envVars.split(",").map((v) => v.trim()).filter(Boolean);
                send({
                  type: "settings/mcpUpsert",
                  name,
                  entry: buildMcpEntry({ transport, commandLine: commandOrUrl, url: commandOrUrl, envVarNames }),
                });
                setName(""); setCommandOrUrl(""); setEnvVars("");
              }}
            >
              Add server
            </BtnPrimary>
          </div>
        </CardShell>
      </div>
    </div>
  );
}
