import { useEffect, useMemo, useState } from "react";
import { ENV_FLAG_OPTIONS, PROVIDERS, type SettingsOutMsg, type SettingsState } from "./types";
import { vscode } from "./vscodeApi";

type McpTransport = "stdio" | "http" | "sse";

function stateDot(state: string): string {
  switch (state) {
    case "connected": return "🟢";
    case "connecting": return "🟡";
    case "failed": return "🔴";
    default: return "⚪";
  }
}

// Mirrors src/mcp-quickpick.ts::splitCommandLine — a bare \s+ split breaks on
// any quoted argument containing a space (e.g. a path under a directory named
// "AI editor") — this repo's own path is a real case. Supports "double" and
// 'single' quoted tokens; unquoted tokens split on whitespace as before.
function splitCommandLine(input: string): string[] {
  const tokens: string[] = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(input)) !== null) {
    const token = match[1] ?? match[2] ?? match[3] ?? "";
    if (token) tokens.push(token);
  }
  return tokens;
}

// Mirrors src/mcp-quickpick.ts::buildMcpEntry (Task 14) — same assembly rules, a
// separate call site (webview form vs. QuickPick command).
function buildMcpEntry(input: {
  transport: McpTransport;
  commandLine: string;
  url: string;
  envVarNames: string[];
}): Record<string, unknown> {
  if (input.transport === "stdio") {
    const [command, ...args] = splitCommandLine(input.commandLine.trim());
    const entry: Record<string, unknown> = { command, args, enabled: true };
    if (input.envVarNames.length) {
      entry.env = Object.fromEntries(input.envVarNames.map((v) => [v, `\${${v}}`]));
    }
    return entry;
  }
  const entry: Record<string, unknown> = { type: input.transport, url: input.url, enabled: true };
  if (input.envVarNames.length) {
    const [first, ...rest] = input.envVarNames;
    const headers: Record<string, string> = { Authorization: `Bearer \${${first}}` };
    for (const name of rest) headers[name] = `\${${name}}`;
    entry.headers = headers;
  }
  return entry;
}

export default function SettingsApp() {
  const [state, setState] = useState<SettingsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [backend, setBackend] = useState(PROVIDERS[0].id);
  const [model, setModel] = useState(PROVIDERS[0].defaultModel);
  const [apiKey, setApiKey] = useState("");

  const [mcpTransport, setMcpTransport] = useState<McpTransport>("stdio");
  const [mcpName, setMcpName] = useState("");
  const [mcpCommandOrUrl, setMcpCommandOrUrl] = useState("");
  const [mcpEnvVars, setMcpEnvVars] = useState("");

  const provider = useMemo(
    () => PROVIDERS.find((p) => p.id === backend) ?? PROVIDERS[0],
    [backend],
  );

  useEffect(() => {
    const onMessage = (event: MessageEvent<SettingsOutMsg>) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;
      setBusy(false);
      if (msg.type === "settings/state") {
        setState(msg.state);
        setError(null);
        if (msg.state.provider) {
          setBackend(msg.state.provider.backend);
          setModel(msg.state.provider.model);
        }
      } else if (msg.type === "settings/error") {
        setError(msg.message);
      }
    };
    window.addEventListener("message", onMessage);
    vscode.postMessage({ type: "settings/load" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const send = (msg: Parameters<typeof vscode.postMessage>[0]) => {
    setBusy(true);
    setError(null);
    vscode.postMessage(msg);
  };

  if (!state) {
    return <div className="p-6 text-sm opacity-70">Loading settings…</div>;
  }

  return (
    <div className="mx-auto flex h-full max-w-2xl flex-col gap-8 overflow-y-auto p-6 text-sm">
      <header>
        <h1 className="text-lg font-semibold">AI Editor Settings</h1>
      </header>

      {error && <p className="text-red-400">{error}</p>}

      {state.restartRequired && (
        <div className="flex items-center justify-between rounded border border-yellow-600 bg-yellow-950/30 px-3 py-2">
          <span>Some changes require a backend restart to take effect.</span>
          <button
            className="rounded bg-yellow-700 px-3 py-1 text-white hover:bg-yellow-600"
            disabled={busy}
            onClick={() => send({ type: "settings/restartBackend" })}
          >
            Restart backend
          </button>
        </div>
      )}

      <section className="flex flex-col gap-3">
        <h2 className="font-semibold">Provider</h2>
        <label className="flex flex-col gap-1">
          <span>Provider</span>
          <select
            className="rounded border border-neutral-600 bg-transparent px-2 py-1.5"
            value={backend}
            onChange={(e) => {
              const next = PROVIDERS.find((p) => p.id === e.target.value)!;
              setBackend(next.id);
              setModel(next.defaultModel);
              setApiKey("");
            }}
          >
            {PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span>Model</span>
          <input
            className="rounded border border-neutral-600 bg-transparent px-2 py-1.5"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </label>
        {!provider.local && (
          <label className="flex flex-col gap-1">
            <span>API key ({provider.keyEnvVar}) — leave blank to keep the stored key</span>
            <input
              type="password"
              className="rounded border border-neutral-600 bg-transparent px-2 py-1.5"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-…"
            />
          </label>
        )}
        <button
          className="self-start rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-500 disabled:opacity-50"
          disabled={busy || !model}
          onClick={() =>
            send({
              type: "settings/setProvider",
              backend,
              model,
              ...(provider.local || !apiKey ? {} : { apiKey }),
            })
          }
        >
          Save &amp; validate
        </button>
        {state.provider && (
          <p className="text-xs opacity-60">
            Active: <code>{state.provider.backend}</code> / <code>{state.provider.model}</code>
          </p>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="font-semibold">Runtime</h2>
        {state.runtime ? (
          <>
            <p className="text-xs opacity-70">Release {state.runtime.releaseTag}</p>
            <ul className="flex flex-col gap-0.5 text-xs opacity-70">
              {Object.entries(state.runtime.components).map(([id, version]) => (
                <li key={id}>{id}: {version}</li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-xs opacity-60">Runtime not installed.</p>
        )}
        <button
          className="self-start rounded border border-neutral-600 px-3 py-1.5 hover:bg-neutral-800"
          disabled={busy}
          onClick={() => send({ type: "settings/restartBackend" })}
        >
          Restart backend
        </button>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="font-semibold">Policies &amp; memory</h2>
        <div className="flex flex-col gap-2">
          {ENV_FLAG_OPTIONS.map((opt) => (
            <label key={opt.key} className="flex items-center justify-between gap-2">
              <span>{opt.label}</span>
              <select
                className="rounded border border-neutral-600 bg-transparent px-2 py-1"
                value={state.envFlags[opt.key] ?? opt.options[0]}
                onChange={(e) => send({ type: "settings/setEnvFlag", key: opt.key, value: e.target.value })}
              >
                {opt.options.map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="font-semibold">MCP servers</h2>
        {!state.mcp.enabled && (
          <p className="text-xs opacity-60">MCP is disabled (AI_EDITOR_MCP_ENABLED=0).</p>
        )}
        <ul className="flex flex-col gap-2">
          {state.mcp.servers.map((s) => (
            <li key={s.name} className="flex flex-col gap-1 rounded border border-neutral-700 p-2">
              <div className="flex items-center gap-2">
                <span>{stateDot(s.state)}</span>
                <span className="font-medium">{s.name}</span>
                <span className="text-xs opacity-60">{s.transport}</span>
                <span className="text-xs opacity-60">{s.toolCount} tools</span>
                <label className="ml-auto flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    checked={s.userEnabled}
                    onChange={(e) =>
                      send({ type: "settings/mcpToggle", name: s.name, enabled: e.target.checked })
                    }
                  />
                  enabled
                </label>
                <button
                  className="rounded border border-neutral-600 px-2 py-0.5 text-xs hover:bg-neutral-800"
                  disabled={busy}
                  onClick={() => send({ type: "settings/mcpReconnect", name: s.name })}
                >
                  Reconnect
                </button>
                <button
                  className="rounded border border-red-700 px-2 py-0.5 text-xs text-red-400 hover:bg-red-950"
                  disabled={busy}
                  onClick={() => send({ type: "settings/mcpDelete", name: s.name })}
                >
                  Remove
                </button>
              </div>
              {s.detail && <p className="text-xs opacity-60">{s.detail}</p>}
            </li>
          ))}
          {state.mcp.servers.length === 0 && (
            <li className="text-xs opacity-60">No MCP servers configured.</li>
          )}
        </ul>

        <div className="flex flex-col gap-2 rounded border border-neutral-700 p-2">
          <h3 className="text-xs font-semibold opacity-80">Add server</h3>
          <div className="flex gap-2">
            <input
              className="w-40 rounded border border-neutral-600 bg-transparent px-2 py-1 text-xs"
              placeholder="name"
              value={mcpName}
              onChange={(e) => setMcpName(e.target.value)}
            />
            <select
              className="rounded border border-neutral-600 bg-transparent px-2 py-1 text-xs"
              value={mcpTransport}
              onChange={(e) => setMcpTransport(e.target.value as McpTransport)}
            >
              <option value="stdio">stdio</option>
              <option value="http">http</option>
              <option value="sse">sse</option>
            </select>
          </div>
          <input
            className="rounded border border-neutral-600 bg-transparent px-2 py-1 text-xs"
            placeholder={mcpTransport === "stdio" ? "command line (e.g. uv run server.py)" : "url"}
            value={mcpCommandOrUrl}
            onChange={(e) => setMcpCommandOrUrl(e.target.value)}
          />
          <input
            className="rounded border border-neutral-600 bg-transparent px-2 py-1 text-xs"
            placeholder="env var names, comma-separated (e.g. GITHUB_PAT)"
            value={mcpEnvVars}
            onChange={(e) => setMcpEnvVars(e.target.value)}
          />
          <button
            className="self-start rounded bg-blue-600 px-3 py-1 text-xs text-white hover:bg-blue-500 disabled:opacity-50"
            disabled={busy || !mcpName || !mcpCommandOrUrl}
            onClick={() => {
              const envVarNames = mcpEnvVars.split(",").map((v) => v.trim()).filter(Boolean);
              const entry = buildMcpEntry({
                transport: mcpTransport,
                commandLine: mcpCommandOrUrl,
                url: mcpCommandOrUrl,
                envVarNames,
              });
              send({ type: "settings/mcpUpsert", name: mcpName, entry });
              setMcpName("");
              setMcpCommandOrUrl("");
              setMcpEnvVars("");
            }}
          >
            Add server
          </button>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="font-semibold">Skills</h2>
        <ul className="flex flex-col gap-1">
          {state.skills.map((s) => (
            <li key={s.name} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={s.enabled}
                onChange={(e) =>
                  send({ type: "settings/skillToggle", name: s.name, enabled: e.target.checked })
                }
              />
              <span className="font-medium">{s.name}</span>
              <span className="text-xs opacity-60">{s.description}</span>
            </li>
          ))}
          {state.skills.length === 0 && (
            <li className="text-xs opacity-60">No skills discovered.</li>
          )}
        </ul>
      </section>
    </div>
  );
}
