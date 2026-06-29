import { useState } from "react";
import { vscode } from "./vscodeApi";
import type { MemoryView } from "./types";

// Slate-dark palette + structure from the approved "Browser tab" wireframe (.superpowers).
const KIND_BG: Record<string, string> = {
  semantic: "#1d4ed8",
  procedural: "#6d28d9",
  episodic: "#0e7490",
};
const KINDS = ["all", "episodic", "semantic", "procedural"] as const;
const SCOPES = ["workspace", "thread"] as const;

function isRetired(m: MemoryView): boolean {
  return m.validTo !== null;
}

function KindBadge({ kind }: { kind: string }) {
  return (
    <span
      className="rounded px-1.5 py-px text-[10px] text-white"
      style={{ background: KIND_BG[kind] ?? "#334155" }}
    >
      {kind}
    </span>
  );
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
    <div data-testid="memory-browser-tab" className="flex h-full flex-col bg-[#0b1220] text-[#cbd5e1]">
      {/* Filter bar — chips + live/retired count (wireframe). */}
      <div className="flex flex-wrap items-center gap-2.5 border-b border-[#1e293b] px-3 py-2 text-[12px]">
        <label className="flex items-center gap-1 rounded-md bg-[#0f172a] px-2.5 py-1">
          scope:
          <select
            className="bg-transparent text-[#cbd5e1] outline-none"
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
        <label className="flex items-center gap-1 rounded-md bg-[#0f172a] px-2.5 py-1">
          kind:
          <select
            className="bg-transparent text-[#cbd5e1] outline-none"
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
        <label className="flex items-center gap-1 rounded-md bg-[#0f172a] px-2.5 py-1">
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
        <span className="ml-auto text-[#94a3b8]">
          {liveCount} live · {retiredCount} retired
        </span>
      </div>

      {/* List + detail split (1.1fr / 1fr, wireframe). */}
      <div className="grid min-h-0 flex-1 gap-3 p-3" style={{ gridTemplateColumns: "1.1fr 1fr" }}>
        <ul data-testid="memory-list" className="flex flex-col gap-1.5 overflow-auto text-[12px]">
          {memories.map((m) => {
            const retired = isRetired(m);
            const isSel = selected === m.id;
            return (
              <li
                key={m.id}
                onClick={() => select(m.id)}
                className={`cursor-pointer rounded-[7px] border p-2 ${
                  isSel ? "border-[#2563eb] bg-[#0b1220]" : "border-[#334155] bg-[#111827]"
                } ${retired ? "opacity-50" : ""}`}
              >
                <KindBadge kind={m.kind} />
                {retired ? (
                  <span className="text-[#fca5a5]"> · retired</span>
                ) : (
                  <span className="text-[#94a3b8]"> · imp {m.importance}</span>
                )}
                &nbsp;
                <span className={retired ? "text-[#94a3b8] line-through" : "text-[#e2e8f0]"}>
                  {m.content}
                </span>
              </li>
            );
          })}
        </ul>

        <div
          data-testid="memory-detail"
          className="overflow-auto rounded-lg border border-[#334155] bg-[#0b1220] p-3 text-[12px]"
        >
          {detail ? (
            <>
              <div className="mb-2">
                <KindBadge kind={detail.kind} />
                {isRetired(detail) ? (
                  <span className="ml-1 rounded bg-[#7f1d1d] px-[7px] py-px text-[11px] text-[#fca5a5]">
                    retired
                  </span>
                ) : (
                  <span className="ml-1 rounded bg-[#065f46] px-[7px] py-px text-[11px] text-[#6ee7b7]">
                    live
                  </span>
                )}
              </div>
              <div className="mb-2.5 whitespace-pre-wrap text-[#e2e8f0]">{detail.content}</div>
              {detail.entities.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1">
                  {detail.entities.map((e) => (
                    <span
                      key={e}
                      className="rounded-[10px] bg-[#1e293b] px-[7px] py-0.5 text-[11px] text-[#93c5fd]"
                    >
                      {e}
                    </span>
                  ))}
                </div>
              )}
              <div className="leading-[1.7] text-[#94a3b8]">
                <b>importance</b> {detail.importance} &nbsp;·&nbsp; <b>scope</b> {detail.scopeKind}
                <br />
                <b>source</b> {detail.sourceKind} · run {detail.sourceRef}
                <br />
                <b>valid_from</b> {detail.validFrom} &nbsp;·&nbsp; <b>segments</b> seq{" "}
                {detail.sourceSeqLo ?? "—"}–{detail.sourceSeqHi ?? "—"}
                {isRetired(detail) && (
                  <>
                    <br />
                    <b>valid_to</b> <span className="text-[#fca5a5]">{detail.validTo}</span>
                  </>
                )}
              </div>

              {chain && chain.length > 1 && (
                <div className="mt-3 border-t border-[#1e293b] pt-2.5">
                  <div className="mb-1.5 text-[11px] tracking-wide text-[#94a3b8]">
                    SUPERSEDE CHAIN
                  </div>
                  <div data-testid="supersede-chain" className="flex flex-col gap-1 text-[#cbd5e1]">
                    {chain.map((c, i) => (
                      <div key={c.id} data-testid={`chain-node-${c.id}`}>
                        {isRetired(c) ? (
                          <span className="opacity-60">
                            <span className="line-through">{c.content}</span>{" "}
                            <span className="text-[#fca5a5]">retired</span>
                          </span>
                        ) : (
                          <span>
                            <b>{c.content}</b> <span className="text-[#6ee7b7]">live</span>
                          </span>
                        )}
                        {i < chain.length - 1 && (
                          <div className="ml-1.5 text-[#64748b]">↓ superseded_by</div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <span className="text-[#64748b]">Select a memory to inspect.</span>
          )}
        </div>
      </div>
    </div>
  );
}
