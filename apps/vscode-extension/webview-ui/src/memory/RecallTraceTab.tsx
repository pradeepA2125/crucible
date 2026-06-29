import type { RecallTrace, RecallTraceEntry, RecallSignals } from "./types";

const SIGNAL_LABELS: (keyof RecallSignals)[] = [
  "semantic",
  "lexical",
  "structural",
  "importance",
  "recency",
];

function SignalBar({ label, value }: { label: string; value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div data-signal={label} className="flex items-center gap-2">
      <span className="w-20 shrink-0 opacity-70">{label}</span>
      <div className="h-2 flex-1 rounded bg-[var(--vscode-panel-border)]">
        <div className="h-2 rounded bg-[var(--vscode-charts-blue)]" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-10 shrink-0 text-right tabular-nums opacity-80">{value.toFixed(2)}</span>
    </div>
  );
}

function Entry({ entry }: { entry: RecallTraceEntry }) {
  // ▲/▼ rank change: the reranker reordered relative to fused order. With entries already
  // in final_rank order, a rerankScore present means the row participated; we surface the
  // arrow whenever a rerank score exists (direction shown vs the fused score).
  const reranked = entry.rerankScore !== null;
  return (
    <div
      data-testid={`trace-entry-${entry.memoryId}`}
      className={`border-b border-[var(--vscode-panel-border)] px-3 py-2 ${entry.injected ? "" : "opacity-50"}`}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="rounded bg-[var(--vscode-badge-background)] px-1.5 text-xs text-[var(--vscode-badge-foreground)]">
          {entry.kind}
        </span>
        <span className="truncate">{entry.content}</span>
        <span className="ml-auto shrink-0 text-xs">
          {entry.injected ? "● injected" : "✗ below floor"}
        </span>
      </div>
      <div className="flex flex-col gap-1">
        {SIGNAL_LABELS.map((label) => (
          <SignalBar key={label} label={label} value={entry.signals[label]} />
        ))}
      </div>
      <div className="mt-1 flex gap-4 text-xs opacity-80">
        <span>fused {entry.fusedScore.toFixed(2)}</span>
        {reranked && (
          <span>
            rerank {entry.rerankScore!.toFixed(2)} {entry.fusedScore <= entry.rerankScore! ? "▲" : "▼"}
          </span>
        )}
      </div>
    </div>
  );
}

export function RecallTraceTab({ trace }: { trace: RecallTrace | null }) {
  if (!trace) {
    return <div className="p-3 opacity-70">No recall recorded yet. Run a chat turn, then Refresh.</div>;
  }
  return (
    <div data-testid="memory-trace-tab">
      <div className="border-b border-[var(--vscode-panel-border)] px-3 py-2 text-xs opacity-80">
        <div className="mb-0.5 font-semibold opacity-100">{trace.query}</div>
        <span>
          {trace.scopeKind}:{trace.scopeId} · {trace.entries.length} candidates ·{" "}
          <span>reranked {trace.reranked ? "✓" : "✗"}</span> · floor {trace.floor.toFixed(2)} · k {trace.k}
        </span>
      </div>
      {trace.entries.length === 0 ? (
        <div className="p-3 opacity-70">0 candidates (recall returned nothing — empty query or no matches).</div>
      ) : (
        trace.entries.map((e) => <Entry key={e.memoryId} entry={e} />)
      )}
    </div>
  );
}
