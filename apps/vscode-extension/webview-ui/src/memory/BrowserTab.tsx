import { useState } from "react";
import { vscode } from "./vscodeApi";
import type { MemoryView } from "./types";

// Filled kind pills reuse the shared tint tokens (settings nav convention, meta.ts) —
// pastel bg + dark text (var(--color-panel)) rather than a one-off saturated hex.
const KIND_TINT: Record<string, string> = {
  semantic: "var(--color-code)",
  procedural: "var(--color-accent)",
  episodic: "var(--color-green)",
};
const KINDS = ["all", "episodic", "semantic", "procedural"] as const;
const SCOPES = ["workspace", "thread"] as const;

function isRetired(m: MemoryView): boolean {
  return m.validTo !== null;
}

function KindBadge({ kind }: { kind: string }) {
  return (
    <span
      className="rounded px-1.5 py-px text-[10px] font-semibold"
      style={{ background: KIND_TINT[kind] ?? "var(--color-text-3)", color: "var(--color-panel)" }}
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
    <div
      data-testid="memory-browser-tab"
      className="flex h-full flex-col"
      style={{ background: "var(--color-panel)", color: "var(--color-text)" }}
    >
      <div
        className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-[12px]"
        style={{ borderBottom: "1px solid var(--color-border)" }}
      >
        <label className="flex items-center gap-1 rounded-md px-2.5 py-1" style={{ background: "var(--color-surface-2)" }}>
          scope:
          <select
            className="bg-transparent outline-none"
            style={{ color: "var(--color-text)" }}
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
        <label className="flex items-center gap-1 rounded-md px-2.5 py-1" style={{ background: "var(--color-surface-2)" }}>
          kind:
          <select
            className="bg-transparent outline-none"
            style={{ color: "var(--color-text)" }}
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
        <label className="flex items-center gap-1 rounded-md px-2.5 py-1" style={{ background: "var(--color-surface-2)" }}>
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
        <span className="ml-auto" style={{ color: "var(--color-text-2)" }}>
          {liveCount} live · {retiredCount} retired
        </span>
      </div>

      <div className="grid min-h-0 flex-1 gap-4 p-4" style={{ gridTemplateColumns: "1.1fr 1fr" }}>
        <ul data-testid="memory-list" className="flex flex-col gap-2 overflow-auto text-[12px]">
          {memories.map((m) => {
            const retired = isRetired(m);
            const isSel = selected === m.id;
            return (
              <li
                key={m.id}
                onClick={() => select(m.id)}
                className={`cursor-pointer rounded-lg border p-2.5 leading-snug ${retired ? "opacity-50" : ""}`}
                style={{
                  borderColor: isSel ? "var(--color-accent-deep)" : "var(--color-border-strong)",
                  background: isSel ? "var(--color-panel)" : "var(--color-surface)",
                }}
              >
                <div className="mb-1 flex items-center gap-1.5">
                  <KindBadge kind={m.kind} />
                  {retired ? (
                    <span style={{ color: "var(--color-red)" }}>retired</span>
                  ) : (
                    <span style={{ color: "var(--color-text-2)" }}>imp {m.importance}</span>
                  )}
                </div>
                <div
                  className={`line-clamp-2 ${retired ? "line-through" : ""}`}
                  style={{ color: retired ? "var(--color-text-2)" : "var(--color-text)" }}
                >
                  {m.content}
                </div>
              </li>
            );
          })}
        </ul>

        <div
          data-testid="memory-detail"
          className="overflow-auto rounded-lg border p-4 text-[12px]"
          style={{ borderColor: "var(--color-border-strong)", background: "var(--color-panel)" }}
        >
          {detail ? (
            <>
              <div className="mb-3">
                <KindBadge kind={detail.kind} />
                {isRetired(detail) ? (
                  <span
                    className="ml-1 rounded px-[7px] py-px text-[11px]"
                    style={{ background: "var(--red-bg)", border: "1px solid var(--red-brd)", color: "var(--color-red)" }}
                  >
                    retired
                  </span>
                ) : (
                  <span
                    className="ml-1 rounded px-[7px] py-px text-[11px]"
                    style={{ background: "var(--green-bg)", border: "1px solid var(--green-brd)", color: "var(--color-green)" }}
                  >
                    live
                  </span>
                )}
              </div>
              <div className="mb-3 whitespace-pre-wrap leading-relaxed" style={{ color: "var(--color-text)" }}>
                {detail.content}
              </div>
              {detail.entities.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-1.5">
                  {detail.entities.map((e) => (
                    <span
                      key={e}
                      className="rounded-[10px] px-[7px] py-0.5 text-[11px]"
                      style={{ background: "var(--color-surface-3)", color: "var(--color-code)" }}
                    >
                      {e}
                    </span>
                  ))}
                </div>
              )}
              <div className="leading-[1.7]" style={{ color: "var(--color-text-2)" }}>
                <b>importance</b> {detail.importance} &nbsp;·&nbsp; <b>scope</b> {detail.scopeKind}
                <br />
                <b>source</b> {detail.sourceKind} · run {detail.sourceRef}
                <br />
                <b>valid_from</b> {detail.validFrom} &nbsp;·&nbsp; <b>segments</b> seq{" "}
                {detail.sourceSeqLo ?? "—"}–{detail.sourceSeqHi ?? "—"}
                {isRetired(detail) && (
                  <>
                    <br />
                    <b>valid_to</b> <span style={{ color: "var(--color-red)" }}>{detail.validTo}</span>
                  </>
                )}
              </div>

              {chain && chain.length > 1 && (
                <div className="mt-4 pt-3" style={{ borderTop: "1px solid var(--color-border)" }}>
                  <div className="mb-2 text-[11px] tracking-wide" style={{ color: "var(--color-text-2)" }}>
                    SUPERSEDE CHAIN
                  </div>
                  <div data-testid="supersede-chain" className="flex flex-col gap-1.5" style={{ color: "var(--color-text)" }}>
                    {chain.map((c, i) => (
                      <div key={c.id} data-testid={`chain-node-${c.id}`}>
                        {isRetired(c) ? (
                          <span className="opacity-60">
                            <span className="line-through">{c.content}</span>{" "}
                            <span style={{ color: "var(--color-red)" }}>retired</span>
                          </span>
                        ) : (
                          <span>
                            <b>{c.content}</b> <span style={{ color: "var(--color-green)" }}>live</span>
                          </span>
                        )}
                        {i < chain.length - 1 && (
                          <div className="ml-1.5" style={{ color: "var(--color-text-3)" }}>↓ superseded_by</div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <span style={{ color: "var(--color-text-3)" }}>Select a memory to inspect.</span>
          )}
        </div>
      </div>
    </div>
  );
}
