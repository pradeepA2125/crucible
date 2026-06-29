import { useState } from "react";
import { vscode } from "./vscodeApi";
import type { MemoryView } from "./types";

const KINDS = ["all", "episodic", "semantic", "procedural"] as const;
const SCOPES = ["workspace", "thread"] as const;

function isRetired(m: MemoryView): boolean {
  return m.validTo !== null;
}

export function BrowserTab({
  memories,
  chains,
}: {
  memories: MemoryView[];
  chains: Record<string, MemoryView[]>;
}) {
  const [scopeKind, setScopeKind] = useState<string>("workspace");
  const [kind, setKind] = useState<string>("all");
  const [includeRetired, setIncludeRetired] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  // scopeId is owned by the host (the workspace path); we emit "" and the host overrides it.
  function emitBrowse(next: { scopeKind?: string; kind?: string; includeRetired?: boolean }) {
    vscode.postMessage({
      type: "browse",
      filter: {
        scopeKind: next.scopeKind ?? scopeKind,
        scopeId: "",
        kind: (next.kind ?? kind) === "all" ? undefined : next.kind ?? kind,
        includeRetired: next.includeRetired ?? includeRetired,
      },
    });
  }

  function select(id: string) {
    setSelected(id);
    vscode.postMessage({ type: "loadChain", memoryId: id });
  }

  const liveCount = memories.filter((m) => !isRetired(m)).length;
  const retiredCount = memories.length - liveCount;
  const detail = memories.find((m) => m.id === selected) ?? null;
  const chain = selected ? chains[selected] ?? null : null;

  return (
    <div data-testid="memory-browser-tab" className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--vscode-panel-border)] px-3 py-2 text-xs">
        <label className="flex items-center gap-1">
          scope
          <select
            value={scopeKind}
            onChange={(e) => {
              setScopeKind(e.target.value);
              emitBrowse({ scopeKind: e.target.value });
            }}
          >
            {SCOPES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          kind
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              emitBrowse({ kind: e.target.value });
            }}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={includeRetired}
            onChange={(e) => {
              setIncludeRetired(e.target.checked);
              emitBrowse({ includeRetired: e.target.checked });
            }}
          />
          include retired
        </label>
        <span className="ml-auto opacity-70">{liveCount} live · {retiredCount} retired</span>
      </div>

      <div className="flex min-h-0 flex-1">
        <ul data-testid="memory-list" className="w-1/2 overflow-auto border-r border-[var(--vscode-panel-border)]">
          {memories.map((m) => (
            <li
              key={m.id}
              onClick={() => select(m.id)}
              className={`cursor-pointer border-b border-[var(--vscode-panel-border)] px-3 py-2 ${
                selected === m.id ? "bg-[var(--vscode-list-activeSelectionBackground)]" : ""
              } ${isRetired(m) ? "line-through opacity-50" : ""}`}
            >
              <div className="flex items-center gap-2">
                <span className="rounded bg-[var(--vscode-badge-background)] px-1.5 text-xs text-[var(--vscode-badge-foreground)]">
                  {m.kind}
                </span>
                <span className="text-xs opacity-70">imp {m.importance}</span>
              </div>
              <div className="truncate text-xs">{m.content}</div>
            </li>
          ))}
        </ul>

        <div data-testid="memory-detail" className="w-1/2 overflow-auto p-3 text-xs">
          {detail ? (
            <>
              <div className="mb-2 whitespace-pre-wrap">{detail.content}</div>
              <div className="mb-2 flex flex-wrap gap-1">
                {detail.entities.map((e) => (
                  <span key={e} className="rounded bg-[var(--vscode-badge-background)] px-1.5 text-[var(--vscode-badge-foreground)]">
                    {e}
                  </span>
                ))}
              </div>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 opacity-80">
                <dt>kind</dt><dd>{detail.kind}</dd>
                <dt>importance</dt><dd>{detail.importance}</dd>
                <dt>source</dt><dd>{detail.sourceKind} ({detail.sourceRef})</dd>
                <dt>seq span</dt><dd>{detail.sourceSeqLo ?? "—"} – {detail.sourceSeqHi ?? "—"}</dd>
                <dt>valid from</dt><dd>{detail.validFrom}</dd>
                <dt>valid to</dt><dd>{detail.validTo ?? "(current)"}</dd>
              </dl>
              {chain && chain.length > 1 && (
                <div data-testid="supersede-chain" className="mt-3">
                  <div className="mb-1 font-semibold">supersede chain</div>
                  <ol className="flex flex-col gap-1">
                    {chain.map((c) => (
                      <li
                        key={c.id}
                        data-testid={`chain-node-${c.id}`}
                        className={c.id === detail.id ? "font-semibold" : "opacity-70"}
                      >
                        {isRetired(c) ? "↳" : "●"} {c.content}
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </>
          ) : (
            <span className="opacity-60">Select a memory to inspect.</span>
          )}
        </div>
      </div>
    </div>
  );
}
