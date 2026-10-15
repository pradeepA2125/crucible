import type { RecallTrace } from "./types";
import { readable } from "./presentation";

export function RecallTraceTab({ trace }: { trace: RecallTrace | null }) {
  if (!trace) return <p className="memory-state">No recall recorded yet. Run a chat turn, then Refresh.</p>;
  const catalog = "version" in trace && trace.rule === "candidate_catalog_v1";
  if ("version" in trace) return <section data-testid="memory-trace-tab">
    <p className="memory-context">Latest recall · {readable(trace.trigger)}</p>
    <p className="memory-status-line">{trace.delivered.length} {catalog ? "offered" : "delivered"} · {trace.candidates.length} candidates</p>
    <p className="memory-muted">{catalog ? "Catalog entries may be false positives. Offering an entry does not mean the model read or used it." : "Delivered means actually sent, not just judged relevant."}</p>
    {catalog && trace.selectionMethod && <p className="memory-muted">Selected using {trace.selectionMethod}.</p>}
    {trace.error && <p role="alert" className="memory-error">{trace.error}</p>}
    <ul className="memory-trace-list">{trace.candidates.map(candidate => {
      const delivered = trace.delivered.includes(candidate.id);
      const combined = trace.scores[candidate.id]?.combined;
      return <li key={candidate.id} data-testid={`trace-entry-${candidate.id}`}>
        <div className="memory-decision"><strong className={delivered ? "memory-delivered" : ""}>{catalog ? delivered ? "Catalog offered" : "Not offered" : delivered ? "Delivered" : "Not delivered"}</strong>
          {combined !== undefined && <span className="memory-score">{catalog ? "BGE ordering" : "combined"}: {combined.toFixed(3)}</span>}
        </div>
        <p className="memory-body">{candidate.catalog}</p>
        {trace.reasons[candidate.id] && <p>{trace.reasons[candidate.id]}</p>}
        <details><summary>Decision evidence</summary><dl className="memory-facts">
          <div><dt>Memory ID</dt><dd>{candidate.id}</dd></div><div><dt>Retrieved by</dt><dd>{candidate.sources.join(", ") || "Not recorded"}</dd></div>
          <div><dt>{catalog ? "Relevance decision" : "Verdict"}</dt><dd>{catalog ? "Left to the main model; no separate judge" : trace.verdicts[candidate.id] === undefined ? "Not recorded" : trace.verdicts[candidate.id] ? "Relevant" : "Not relevant"}</dd></div>
          {catalog && trace.scores[candidate.id]?.combined !== undefined && <div><dt>Score meaning</dt><dd>Ordering only; no acceptance cutoff or calibrated confidence.</dd></div>}
          {Object.entries(trace.scores[candidate.id] ?? {}).filter(([key]) => key !== "combined").map(([key, score]) => <div key={key}><dt>{key}</dt><dd>{score.toFixed(3)}</dd></div>)}
          {trace.invalidatedIds.includes(candidate.id) && <div><dt>Revalidation</dt><dd>Invalidated before delivery</dd></div>}
        </dl></details>
      </li>;
    })}</ul>
    {!trace.candidates.length && <p className="memory-state">No candidates for this trigger.</p>}
    {trace.delivered.some(id => !trace.candidates.some(c => c.id === id)) && <p className="memory-muted">Delivered IDs without candidate details: {trace.delivered.filter(id => !trace.candidates.some(c => c.id === id)).join(", ")}</p>}
    <details><summary>Raw recall signals & rule</summary>
      <p>Status: {trace.status} · judge: {trace.judge}</p>
      {trace.rule && <p>Rule: {trace.rule}</p>}
      <dl className="memory-facts">{Object.entries(trace.signals).map(([name, value]) => <div key={name}><dt>{({ Q: "Request (Q)", C: "Closing (C)", R: "Recap (R)" } as Record<string, string>)[name] ?? name}</dt><dd>{value || "Not provided"}</dd></div>)}</dl>
      {trace.invalidIds.length > 0 && <p>Invalid IDs: {trace.invalidIds.join(", ")}</p>}
      {trace.invalidatedIds.length > 0 && <p>Invalidated IDs: {trace.invalidatedIds.join(", ")}</p>}
    </details>
    {trace.rerankInput && <details><summary>Combined reranker input · {trace.rerankInput.maxPairTokens} tokens maximum</summary>
      <pre>{trace.rerankInput.query}</pre><p>Shortened context: {trace.rerankInput.truncatedSignals.join(", ") || "none"}</p><p>Shortened memories: {trace.rerankInput.truncatedMemoryIds.join(", ") || "none"}</p>
    </details>}
  </section>;
  return <section data-testid="memory-trace-tab" aria-label="Recall decisions">
    <p className="memory-body">{trace.query}</p><p className="memory-muted">Legacy recall · {trace.entries.length} candidates · scope: {trace.scopeKind} · reranked {trace.reranked ? "✓" : "✗"}</p>
    {!trace.entries.length && <p className="memory-state">0 candidates (recall returned nothing).</p>}
    <ul className="memory-trace-list">{trace.entries.map(e => <li key={e.memoryId} data-testid={`trace-entry-${e.memoryId}`}>
      <div className="memory-decision"><strong className={e.injected ? "memory-delivered" : ""}>{e.injected ? "Delivered · injected" : "Not delivered"}</strong><span className="memory-score">Fused score {e.fusedScore.toFixed(3)}</span>{e.rerankScore !== null && <span className="memory-score">Rerank {e.rerankScore.toFixed(3)}</span>}</div>
      <p className="memory-body">{e.content}</p><p className="memory-muted">{e.kind} · rank {e.finalRank + 1}</p>
      {!e.injected && <p className="memory-muted">{e.fusedScore < trace.floor ? "Fused score below floor" : "Not selected for delivery; no further reason recorded."}</p>}
      <details><summary>Raw ranking signals</summary><dl className="memory-facts">{Object.entries(e.signals).map(([name, value]) => <div key={name} data-signal={name}><dt>{name}</dt><dd>{value.toFixed(3)}</dd></div>)}<div><dt>Floor</dt><dd>{trace.floor}</dd></div><div><dt>Limit</dt><dd>{trace.k}</dd></div><div><dt>Memory ID</dt><dd>{e.memoryId}</dd></div></dl></details>
    </li>)}</ul>
  </section>;
}
