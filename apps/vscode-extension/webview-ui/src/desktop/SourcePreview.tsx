import { useEffect, useMemo, useState } from "react";
/** Uses the transcript's mono/code surface tokens; no separate syntax-highlighter exists. */
export function SourcePreview({ path, text, wrap }: { path: string; text: string; wrap: boolean }) {
  const lines = useMemo(() => text.split("\n"), [text]), [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [path, text]);
  useEffect(()=>{window.dispatchEvent(new Event("desktop/findTargetChanged"));},[path,text,page]);
  const start = Math.min(page * 2000, Math.max(0, lines.length - 1)), shown = lines.slice(start, start + 2000);
  return <div className="desktop-source message-markdown">
    {lines.length > 2000 && <div className="desktop-source-pages"><span>Lines {start + 1}–{start + shown.length} of {lines.length.toLocaleString()}</span><button type="button" disabled={!page} onClick={() => setPage(value => value - 1)}>Previous lines</button><button type="button" disabled={start + shown.length >= lines.length} onClick={() => setPage(value => value + 1)}>Next lines</button></div>}
    <pre className={`mono${wrap ? " is-wrapped" : ""}`} tabIndex={0} aria-label={`Read-only source ${path}`}><code>{shown.map((line, index) => <span key={start + index} className="desktop-source-line"><span aria-hidden="true" className="desktop-line-number">{start + index + 1}</span><span className="desktop-line-content">{line}{index < shown.length - 1 ? "\n" : ""}</span></span>)}</code></pre>
  </div>;
}
