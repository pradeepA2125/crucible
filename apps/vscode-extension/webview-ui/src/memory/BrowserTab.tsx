import { useRef, useState } from "react";
import { vscode } from "./vscodeApi";
import { memoryTime, provenanceLabel } from "./presentation";
import type { MemoryView, MemorySnapshot, DurableMemoryView } from "./types";

export function BrowserTab({ memories, chains, snapshot, loading = false, error }: {
  memories: MemoryView[]; chains: Record<string, MemoryView[]>;
  snapshot?: MemorySnapshot | null; loading?: boolean; error?: string | null;
}) {
  const durable = snapshot?.pipeline === "durable";
  const [scope, setScope] = useState("all");
  const [kind, setKind] = useState("all");
  const [state, setState] = useState("live");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const records = durable ? snapshot.memories.map(m => ({
    id: m.id, content: m.content, kind: m.kind, scope: m.scope_kind, retired: m.valid_to !== null,
    entities: m.entities, extra: [m.topic, m.applies_when, m.detail, m.provenance].join(" "), durable: m,
    legacy: null as MemoryView | null,
  })) : memories.map(m => ({ id: m.id, content: m.content, kind: m.kind, scope: m.scopeKind,
    retired: m.validTo !== null, entities: m.entities, extra: m.sourceRef,
    durable: null as DurableMemoryView | null, legacy: m }));
  const visible = records.filter(m => (scope === "all" || m.scope === scope)
    && (kind === "all" || m.kind === kind)
    && (state === "all" || m.retired === (state === "retired"))
    && [m.content, m.extra, m.id, ...m.entities].join(" ").toLowerCase().includes(search.toLowerCase()));
  const detail = visible.find(m => m.id === selected);
  function select(id: string) {
    setSelected(id);
    if (!durable) vscode.postMessage({ type: "loadChain", memoryId: id });
    requestAnimationFrame(() => detailRef.current?.focus());
  }
  function back() {
    setSelected(null);
    const id = selected;
    requestAnimationFrame(() => Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>("button") ?? []).find(button => button.dataset.memoryId === id)?.focus());
  }
  if (error) return <div role="alert" className="memory-state memory-error">Couldn’t load saved memories. {error}</div>;
  if (loading) return <div role="status" className="memory-state">Loading saved memories…</div>;
  if (snapshot && snapshot.status !== "ready") return <div className="memory-state">
    {snapshot.status === "disabled" ? "Memory is disabled for this backend." : "The active memory store is unavailable. No legacy data is substituted."}
  </div>;
  return <section data-testid="memory-browser-tab" aria-label="Saved memories">
    <p className="memory-context">{durable ? "Durable store · user, repository & current conversation" : "Legacy store · workspace & conversation"}</p>
    <div className="memory-toolbar">
      <label className="memory-search">Search memories<input type="search" value={search} placeholder="Claim, body, entity or ID" onChange={e => setSearch(e.target.value)} /></label>
      <label>Scope<select value={scope} onChange={e => {
        setScope(e.target.value); setSelected(null);
        if (!durable && e.target.value !== "all") vscode.postMessage({ type: "browse", filter: { scopeKind: e.target.value, scopeId: "", includeRetired: true } });
      }}><option value="all">{durable ? "All visible scopes" : "Loaded scope"}</option>
        {(durable ? ["user", "repo", "thread"] : ["workspace", "thread"]).map(s => <option key={s}>{s}</option>)}
      </select></label>
      <label>Kind<select value={kind} onChange={e => setKind(e.target.value)}>{["all", "semantic", "procedural", "episodic"].map(k => <option key={k}>{k}</option>)}</select></label>
      <label>State<select value={state} onChange={e => setState(e.target.value)}><option value="live">Live</option><option value="retired">Retired</option><option value="all">All states</option></select></label>
    </div>
    <p className="memory-muted" role="status">{visible.length} of {records.length} loaded memories</p>
    {!visible.length ? <div className="memory-state">{records.length ? "No memories match these filters." : "No saved memories in this scope."}</div> :
      <div className={`memory-split ${detail ? "has-selection" : ""}`}>
        <ul ref={listRef} className="memory-list" data-testid="memory-list" aria-label="Memory results" onKeyDown={e => {
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
          const buttons = Array.from(listRef.current?.querySelectorAll("button") ?? []);
          const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
          const next = e.key === "Home" ? 0 : e.key === "End" ? buttons.length - 1 : Math.max(0, Math.min(buttons.length - 1, index + (e.key === "ArrowDown" ? 1 : -1)));
          e.preventDefault(); buttons[next]?.focus();
        }}>
          {visible.map(m => <li key={m.id}><button className="memory-row" data-memory-id={m.id} aria-pressed={detail?.id === m.id} onClick={() => select(m.id)}>
            <span className="memory-row-title">{m.content}</span><span className="memory-meta"><span>{m.kind}</span><span>{m.scope}</span><span>{m.retired ? "Retired" : "Live"}</span></span>
          </button></li>)}
        </ul>
        <section ref={detailRef} tabIndex={-1} className="memory-detail" data-testid="memory-detail" aria-label="Memory detail" onKeyDown={e => { if (e.key === "Escape") back(); }}>
          {detail ? <>
            <button className="memory-action memory-narrow-back" onClick={back}>← Back to memories</button>
            <h2 className="memory-body memory-claim">{detail.content}</h2>
            <p className="memory-muted">{detail.kind} · {detail.scope} · {detail.retired ? "Retired" : "Live"}</p>
            {detail.durable && <><h3>Applies when</h3><p className="memory-body">{detail.durable.applies_when}</p>
              {detail.durable.detail && <><h3>Explanation</h3><p className="memory-body">{detail.durable.detail}</p></>}
            </>}
            {detail.entities.length > 0 && <><h3>Related entities</h3><p className="memory-body">{detail.entities.join(" · ")}</p></>}
            <div className="memory-provenance"><h3>Source</h3>
              <p>{detail.durable ? provenanceLabel(detail.durable.provenance) : provenanceLabel(detail.legacy!.sourceKind)}</p>
              <p className="memory-muted">{detail.durable ? `Conversation reference · message ${detail.durable.source_message_id}` : "Legacy source reference"}</p>
              <p className="memory-muted">Saved {memoryTime(detail.durable?.created_at ?? detail.legacy!.createdAt)}</p>
              <p className="memory-muted">Read-only reference. Source navigation is not available in this inspector.</p>
            </div>
            <details><summary>Technical identifiers & lifecycle</summary><dl className="memory-facts">
              <div><dt>ID</dt><dd>{detail.id}</dd></div><div><dt>Scope ID</dt><dd>{detail.durable?.scope_id ?? detail.legacy?.scopeId}</dd></div>
              <div><dt>Source reference</dt><dd>{detail.durable ? detail.durable.source_thread_id : detail.legacy!.sourceRef}</dd></div>
              {detail.durable && <><div><dt>Topic</dt><dd>{detail.durable.topic}</dd></div><div><dt>Revision</dt><dd>{detail.durable.revision}</dd></div><div><dt>Provenance code</dt><dd>{detail.durable.provenance}</dd></div></>}
              {detail.legacy && <div><dt>Segments</dt><dd>{detail.legacy.sourceSeqLo ?? "—"}–{detail.legacy.sourceSeqHi ?? "—"}</dd></div>}
              <div><dt>Valid from</dt><dd>{memoryTime(detail.durable?.valid_from ?? detail.legacy!.validFrom)}</dd></div>
              {detail.retired && <div><dt>Retired at</dt><dd>{memoryTime((detail.durable?.valid_to ?? detail.legacy?.validTo)!)}</dd></div>}
              {(detail.durable?.superseded_by ?? detail.legacy?.supersededBy) && <div><dt>Replaced by</dt><dd>{detail.durable?.superseded_by ?? detail.legacy?.supersededBy}</dd></div>}
            </dl></details>
            {!durable && chains[detail.id] && chains[detail.id]!.length > 1 && <details><summary>Revision chain</summary><ol data-testid="supersede-chain">{chains[detail.id]!.map(m => <li key={m.id} data-testid={`chain-node-${m.id}`}>{m.content} · {m.validTo ? "Retired" : "Live"}</li>)}</ol></details>}
          </> : <p className="memory-muted">Select a memory to inspect.</p>}
        </section>
      </div>}
  </section>;
}