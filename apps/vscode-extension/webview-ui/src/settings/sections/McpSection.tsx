import { useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { Switch } from "../../components/shared/Switch";
import { SectionHeader } from "../SectionHeader";
import { buildMcpEntry, type McpTransport } from "../mcpEntry";
import { FIELD } from "../ui";
import type { SectionProps } from "./meta";

/** Colored status dot; the transient "connecting" state pulses a ring. */
function StatusDot({ state }: { state: string }) {
  const color =
    state === "connected" ? "var(--color-green)"
    : state === "connecting" ? "var(--color-amber)"
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

/** McpSection — server list (status dot, tool count, switch, reconnect/remove) + add form. */
export function McpSection({ state, busy, send }: SectionProps) {
  const [filter, setFilter] = useState("");
  const [transport, setTransport] = useState<McpTransport>("stdio");
  const [name, setName] = useState("");
  const [commandOrUrl, setCommandOrUrl] = useState("");
  const [envVars, setEnvVars] = useState("");

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
              subtitle={`${s.transport} · ${s.toolCount} tools`}
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
              placeholder="env var names, comma-separated (e.g. GITHUB_PAT)"
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
