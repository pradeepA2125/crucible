import type { RecallTrace, RecallTraceEntry, RecallSignals } from "./types";

const KIND_TINT: Record<string, string> = {
  semantic: "var(--color-code)",
  procedural: "var(--color-accent)",
  episodic: "var(--color-green)",
};

const SIGNALS: { key: keyof RecallSignals; label: string; color: string }[] = [
  { key: "semantic", label: "semantic", color: "var(--color-code)" },
  { key: "lexical", label: "lexical", color: "var(--color-code)" },
  { key: "structural", label: "structural", color: "var(--color-code)" },
  { key: "importance", label: "importance", color: "var(--color-accent-deep)" },
  { key: "recency", label: "recency", color: "var(--color-green)" },
];

function KindBadge({ kind }: { kind: string }) {
  return (
    <span
      className="rounded px-[7px] py-px text-[11px] font-semibold"
      style={{ background: KIND_TINT[kind] ?? "var(--color-text-3)", color: "var(--color-panel)" }}
    >
      {kind}
    </span>
  );
}

function SignalCell({ label, value, color }: { label: string; value: number; color: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div data-signal={label} className="flex flex-col gap-1">
      <span className="flex justify-between text-[10px]" style={{ color: "var(--color-text-2)" }}>
        <span>{label}</span>
        <span className="tabular-nums" style={{ color: "var(--color-text)" }}>{value.toFixed(2)}</span>
      </span>
      <div className="h-1.5 rounded-full" style={{ background: "var(--color-border)" }}>
        <div className="h-1.5 rounded-full" style={{ width: `${pct}%`, background: color }} />
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
  let rankSuffix = "";
  if (reranked && fusedRank !== entry.finalRank) {
    rankSuffix = ` ${entry.finalRank < fusedRank ? "▲" : "▼"} from #${fusedRank + 1}`;
  }

  return (
    <div
      data-testid={`trace-entry-${entry.memoryId}`}
      className={`mb-3 rounded-lg border p-3.5 ${entry.injected ? "" : "opacity-55"}`}
      style={{ borderColor: "var(--color-border-strong)", background: "var(--color-surface)" }}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <span className="min-w-0 flex items-center gap-2">
          <KindBadge kind={entry.kind} />
          <span className="line-clamp-2 leading-snug" style={{ color: "var(--color-text)" }}>{entry.content}</span>
        </span>
        {entry.injected ? (
          <span
            className="shrink-0 whitespace-nowrap rounded px-2 py-0.5 text-[11px]"
            style={{ background: "var(--green-bg)", border: "1px solid var(--green-brd)", color: "var(--color-green)" }}
          >
            ✓ injected · #{entry.finalRank + 1}
            {rankSuffix}
          </span>
        ) : (
          <span
            className="shrink-0 whitespace-nowrap rounded px-2 py-0.5 text-[11px]"
            style={{ background: "var(--red-bg)", border: "1px solid var(--red-brd)", color: "var(--color-red)" }}
          >
            ✗ below floor
          </span>
        )}
      </div>

      <div
        className="grid items-end gap-x-4 gap-y-2"
        style={{ gridTemplateColumns: `repeat(5,1fr) auto${reranked ? " auto" : ""}` }}
      >
        {SIGNALS.map((s) => (
          <SignalCell key={s.key} label={s.label} value={entry.signals[s.key]} color={s.color} />
        ))}
        <div className="pl-4 text-right text-[10px]" style={{ borderLeft: "1px solid var(--color-border)", color: "var(--color-text-2)" }}>
          fused
          <br />
          <b className="text-[13px]" style={{ color: "var(--color-text)" }}>{entry.fusedScore.toFixed(3)}</b>
        </div>
        {reranked && (
          <div className="text-right text-[10px]" style={{ color: "var(--color-text-2)" }}>
            rerank
            <br />
            <b className="text-[13px]" style={{ color: "var(--color-green)" }}>
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
      <div className="p-3" style={{ color: "var(--color-text-2)" }}>
        No recall recorded yet. Run a chat turn, then Refresh.
      </div>
    );
  }

  const byFused = [...trace.entries].sort((a, b) => b.fusedScore - a.fusedScore);
  const fusedRankOf = new Map(byFused.map((e, i) => [e.memoryId, i]));

  return (
    <div data-testid="memory-trace-tab" className="p-4" style={{ background: "var(--color-panel)", color: "var(--color-text)" }}>
      <div
        className="mb-4 rounded-md px-3.5 py-2.5 text-[12px] leading-relaxed"
        style={{ background: "var(--color-surface-2)", color: "var(--color-text)" }}
      >
        <b>query</b> "{trace.query}" &nbsp;·&nbsp; <b>scope</b> {trace.scopeKind} &nbsp;·&nbsp;{" "}
        <b>{trace.entries.length} candidates</b> &nbsp;·&nbsp;{" "}
        <span style={{ color: trace.reranked ? "var(--color-green)" : "var(--color-text-2)" }}>
          reranked {trace.reranked ? "✓" : "✗"}
        </span>{" "}
        &nbsp;·&nbsp; <b>floor</b> {trace.floor.toFixed(2)} &nbsp;·&nbsp; <b>k</b> {trace.k}
      </div>

      {trace.entries.length === 0 ? (
        <div style={{ color: "var(--color-text-2)" }}>
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
              className="mb-3 rounded-lg border p-3.5 opacity-55"
              style={{ borderColor: "var(--color-border-strong)", background: "var(--color-surface)" }}
            >
              <div className="mb-2 flex items-start justify-between gap-3">
                <span className="min-w-0 flex items-center gap-2">
                  <KindBadge kind={e.kind} />
                  <span className="line-clamp-2 leading-snug" style={{ color: "var(--color-text)" }}>{e.content}</span>
                </span>
                <span
                  className="shrink-0 whitespace-nowrap rounded px-2 py-0.5 text-[11px]"
                  style={{ background: "var(--red-bg)", border: "1px solid var(--red-brd)", color: "var(--color-red)" }}
                >
                  ✗ below floor
                </span>
              </div>
              <div style={{ color: "var(--color-text-3)" }}>
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
