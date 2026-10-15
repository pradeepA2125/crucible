import { useEffect, useState } from "react";
import { vscode } from "./vscodeApi";
import type { HostToMemory, RecallTrace, MemoryView, CurationStatus, MemorySnapshot } from "./types";
import { RecallTraceTab } from "./RecallTraceTab";
import { BrowserTab } from "./BrowserTab";
import "./inspector.css";
import { memoryTime, readable } from "./presentation";

type Tab = "browser" | "trace" | "curation";
export default function MemoryApp() {
  const [tab, setTab] = useState<Tab>("browser");
  const [trace, setTrace] = useState<RecallTrace | null>(null);
  const [memories, setMemories] = useState<MemoryView[]>([]);
  const [snapshot, setSnapshot] = useState<MemorySnapshot | null>(null);
  const [chains, setChains] = useState<Record<string, MemoryView[]>>({});
  const [curation, setCuration] = useState<CurationStatus | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState({ browser: true, trace: true, curation: true });
  useEffect(() => {
    function onMessage(ev: MessageEvent<HostToMemory>) {
      const msg = ev.data;
      if (!msg || typeof msg !== "object") return;
      const done = (resource: "browser" | "trace" | "curation") => {
        setLoading(l => ({ ...l, [resource]: false }));
        setErrors(e => { const next = { ...e }; delete next[resource]; return next; });
      };
      if (msg.type === "trace") { setTrace(msg.trace); done("trace"); }
      else if (msg.type === "snapshot") {
        setSnapshot(msg.snapshot);
        if (msg.snapshot.pipeline !== "legacy" || msg.snapshot.status !== "ready") done("browser");
      } else if (msg.type === "list") { setMemories(msg.memories); done("browser"); }
      else if (msg.type === "curation") {
        setCuration(msg.status); done("curation");
        if (msg.error) setErrors(e => ({ ...e, curation: msg.error! }));
      } else if (msg.type === "chain") setChains(c => ({ ...c, [msg.memoryId]: msg.chain }));
      else if (msg.type === "error") {
        const resource = msg.resource ?? "browser";
        setErrors(e => ({ ...e, [resource]: msg.message }));
        setLoading(l => ({ ...l, [resource]: false }));
      }
    }
    window.addEventListener("message", onMessage);
    vscode.postMessage({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, []);
  const refresh = () => {
    setErrors({}); setChains({});
    setLoading({ browser: true, trace: true, curation: true });
    vscode.postMessage({ type: "refresh" });
  };
  return <main className="memory-inspector"><div className="memory-shell">
    <header><div className="memory-heading"><h1>Memory inspector</h1><span className="memory-muted">Read only</span><button className="memory-action" onClick={refresh}>Refresh</button></div>
    </header>
    <nav className="memory-nav" aria-label="Memory inspector sections">
      {([["browser", "Saved memories"], ["trace", "Recall decisions"], ["curation", "Curation"]] as const).map(([id, title]) =>
        <button key={id} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>{title}</button>)}
    </nav>
    {tab === "browser" && <><BrowserTab memories={memories} chains={chains} snapshot={snapshot} loading={loading.browser} error={errors.browser} />
      {errors.chain && <p role="alert" className="memory-error">Revision chain unavailable: {errors.chain}</p>}</>}
    {tab === "trace" && (loading.trace ? <p role="status" className="memory-state">Loading recall decisions…</p> : errors.trace ? <p role="alert" className="memory-error">{errors.trace}</p> : snapshot?.status === "disabled" ? <p className="memory-state">Memory recall is disabled.</p> : <RecallTraceTab trace={trace} />)}
    {tab === "curation" && <section data-testid="curation-status" aria-label="Curation"><p className="memory-context">Background publication for this conversation</p>
      {loading.curation ? <p role="status" className="memory-state">Loading curation status…</p> : errors.curation ? <p role="alert" className="memory-error">{errors.curation}</p> : !curation ? <p className="memory-state">Curation status is unavailable from this backend.</p> : !curation.enabled ? <p className="memory-state">Idle curation is disabled.</p> : <>
        <p className="memory-status-line">Status: {readable(curation.status)}</p>
        {curation.error && <p role="alert" className="memory-error">{curation.error}</p>}
        {curation.retryAt && <p>Retry after {memoryTime(curation.retryAt)}.</p>}
        <dl className="memory-facts"><div><dt>Progress</dt><dd>Processed through source message {curation.lastMseq}.</dd></div>
          {curation.updatedAt && <div><dt>Updated</dt><dd><time dateTime={curation.updatedAt}>{memoryTime(curation.updatedAt)}</time></dd></div>}</dl>
        <details><summary>Last processing details</summary><dl className="memory-facts"><div><dt>Saved</dt><dd>{curation.saved}</dd></div><div><dt>Dropped</dt><dd>{curation.dropped}</dd></div><div><dt>Chunks</dt><dd>{curation.chunks}</dd></div><div><dt>Failures</dt><dd>{curation.failures}</dd></div></dl></details>
      </>}
    </section>}
  </div></main>;
}