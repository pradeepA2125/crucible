import type { RecallTrace, RecallTraceEntry, RecallSignals } from "./types";

// Slate-dark palette from the approved design wireframe (.superpowers brainstorm,
// "Memory Inspector — proposed layout", locked as Layout A with full-word labels).
const KIND_BG: Record<string, string> = {
  semantic: "#1d4ed8",
  procedural: "#6d28d9",
  episodic: "#0e7490",
};

// Per-signal bar colours (wireframe): retrieval signals blue, importance purple, recency green.
const SIGNALS: { key: keyof RecallSignals; label: string; color: string }[] = [
  { key: "semantic", label: "semantic", color: "#3b82f6" },
  { key: "lexical", label: "lexical", color: "#3b82f6" },
  { key: "structural", label: "structural", color: "#3b82f6" },
  { key: "importance", label: "importance", color: "#8b5cf6" },
  { key: "recency", label: "recency", color: "#10b981" },
];

function KindBadge({ kind }: { kind: string }) {
  return (
    <span
      className="rounded px-[7px] py-px text-[11px] text-white"
      style={{ background: KIND_BG[kind] ?? "#334155" }}
    >
      {kind}
    </span>
  );
}

function SignalCell({ label, value, color }: { label: string; value: number; color: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div data-signal={label} className="text-[10px] text-[#94a3b8]">
      {label}
      <div className="mt-0.5 h-[5px] rounded bg-[#1e293b]">
        <div className="h-[5px] rounded" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function Entry({
  entry,
  reranked,
  fusedRank,
}: {
  entry: RecallTraceEntry;
  reranked: boolean;
  fusedRank: number;
}) {
  // Rank label mirrors the wireframe: "✓ injected · #1", and on a rerank move "· #2 ▲ from #4".
  let rankSuffix = "";
  if (reranked && fusedRank !== entry.finalRank) {
    rankSuffix = ` ${entry.finalRank < fusedRank ? "▲" : "▼"} from #${fusedRank + 1}`;
  }

  return (
    <div
      data-testid={`trace-entry-${entry.memoryId}`}
      className={`mb-2 rounded-lg border border-[#334155] bg-[#111827] p-2.5 ${entry.injected ? "" : "opacity-55"}`}
    >
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="truncate">
          <KindBadge kind={entry.kind} /> <span className="text-[#cbd5e1]">{entry.content}</span>
        </span>
        {entry.injected ? (
          <span className="shrink-0 rounded bg-[#065f46] px-2 py-px text-[11px] text-[#6ee7b7]">
            ✓ injected · #{entry.finalRank + 1}
            {rankSuffix}
          </span>
        ) : (
          <span className="shrink-0 rounded bg-[#7f1d1d] px-2 py-px text-[11px] text-[#fca5a5]">
            ✗ below floor
          </span>
        )}
      </div>

      <div
        className="grid items-end gap-2"
        style={{ gridTemplateColumns: `repeat(5,1fr) auto${reranked ? " auto" : ""}` }}
      >
        {SIGNALS.map((s) => (
          <SignalCell key={s.key} label={s.label} value={entry.signals[s.key]} color={s.color} />
        ))}
        <div className="text-right text-[10px] text-[#94a3b8]">
          fused
          <br />
          <b className="text-[#e2e8f0]">{entry.fusedScore.toFixed(3)}</b>
        </div>
        {reranked && (
          <div className="text-right text-[10px] text-[#94a3b8]">
            rerank
            <br />
            <b className="text-[#34d399]">
              {entry.rerankScore !== null ? `${entry.rerankScore.toFixed(2)} ▲` : "—"}
            </b>
          </div>
        )}
      </div>
    </div>
  );
}

export function RecallTraceTab({ trace }: { trace: RecallTrace | null }) {
  if (!trace) {
    return (
      <div className="p-3 text-[#94a3b8]">No recall recorded yet. Run a chat turn, then Refresh.</div>
    );
  }

  // Fused-order ranking, so a reranked row can show "▲ from #N" vs its pre-rerank position.
  const byFused = [...trace.entries].sort((a, b) => b.fusedScore - a.fusedScore);
  const fusedRankOf = new Map(byFused.map((e, i) => [e.memoryId, i]));

  return (
    <div data-testid="memory-trace-tab" className="bg-[#0b1220] p-3 text-[#cbd5e1]">
      <div className="mb-3 rounded-md bg-[#0f172a] px-2.5 py-2 text-[12px] text-[#cbd5e1]">
        <b>query</b> "{trace.query}" &nbsp;·&nbsp; <b>scope</b> {trace.scopeKind} &nbsp;·&nbsp;{" "}
        <b>{trace.entries.length} candidates</b> &nbsp;·&nbsp;{" "}
        <span className={trace.reranked ? "text-[#34d399]" : "text-[#94a3b8]"}>
          reranked {trace.reranked ? "✓" : "✗"}
        </span>{" "}
        &nbsp;·&nbsp; <b>floor</b> {trace.floor.toFixed(2)} &nbsp;·&nbsp; <b>k</b> {trace.k}
      </div>

      {trace.entries.length === 0 ? (
        <div className="text-[#94a3b8]">
          0 candidates (recall returned nothing — empty query or no matches).
        </div>
      ) : (
        trace.entries.map((e) =>
          e.injected ? (
            <Entry
              key={e.memoryId}
              entry={e}
              reranked={trace.reranked}
              fusedRank={fusedRankOf.get(e.memoryId) ?? e.finalRank}
            />
          ) : (
            <div
              key={e.memoryId}
              data-testid={`trace-entry-${e.memoryId}`}
              className="mb-2 rounded-lg border border-[#334155] bg-[#111827] p-2.5 opacity-55"
            >
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <span className="truncate">
                  <KindBadge kind={e.kind} /> <span className="text-[#cbd5e1]">{e.content}</span>
                </span>
                <span className="shrink-0 rounded bg-[#7f1d1d] px-2 py-px text-[11px] text-[#fca5a5]">
                  ✗ below floor
                </span>
              </div>
              <div className="text-[#64748b]">
                fused <b>{e.fusedScore.toFixed(2)}</b> &lt; floor {trace.floor.toFixed(2)} — not
                injected
              </div>
            </div>
          )
        )
      )}
    </div>
  );
}
