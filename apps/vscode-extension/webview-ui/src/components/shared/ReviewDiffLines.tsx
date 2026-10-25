import { Fragment, memo, useContext, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { ReviewDraft, reviewLineLabel, reviewCommentText, reviewExcerpt, type ReviewCommentSource } from "../../drafts/reviewDraft";
import type { DiffLine } from "./DiffPanes";
/** Small bounded lexical colouring, never HTML. Unknown languages/long suffixes stay plain. */
function code(text: string, path: string, highlight: boolean): ReactNode {
  if (!highlight || !/\.(tsx?|jsx?|py|rs|go|java|json|css|sh|ya?ml)$/i.test(path)) return text;
  const prefix = text.slice(0, 1500), parts: ReactNode[] = []; let cursor = 0;
  const expression = /\/\/.*$|#.*$|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_$][\w$]*\b/g;
  for (const token of prefix.matchAll(expression)) {
    const start = token.index!, word = token[0]; parts.push(prefix.slice(cursor, start));
    const kind = (/^#/.test(word) && /\.(py|sh|ya?ml)$/i.test(path) || /^\/\//.test(word) && /\.(tsx?|jsx?|rs|go|java)$/i.test(path)) ? "comment" : /^["']/.test(word) ? "string" : /^\d/.test(word) ? "literal" : /^(import|from|export|const|let|var|class|interface|type|return|async|await|if|else|for|while|def|fn|pub|use|try|catch|throw|new|function|extends|implements|true|false|null|undefined)$/.test(word) ? "keyword" : /^[A-Z]/.test(word) ? "type" : undefined;
    parts.push(kind ? <span key={start} className={`review-code-${kind}`}>{word}</span> : word); cursor = start + word.length;
  }
  parts.push(prefix.slice(cursor), text.slice(prefix.length)); return parts;
}
function Context({ lines, path, render, onFold }: { lines: DiffLine[]; path: string; render(line: DiffLine, key: string): ReactNode; onFold?(): void }) {
  const [expanded, setExpanded] = useState(false);
  if (lines.length <= 8) return <>{lines.map((line, i) => render(line, `${path}-${i}`))}</>;
  return <>{(expanded ? lines : lines.slice(0, 3)).map((line, i) => render(line, `start-${i}`))}
    <button type="button" className="review-context-fold" aria-expanded={expanded} onClick={() => { setExpanded(!expanded); onFold?.(); }}>{expanded ? "Collapse unchanged context" : `${lines.length - 6} unchanged lines · expand`}</button>
    {!expanded && lines.slice(-3).map((line, i) => render(line, `end-${i}`))}</>;
}
export const ReviewDiffLines = memo(function ReviewDiffLines({ lines, path, wrap, source }: { lines: DiffLine[]; path: string; wrap: boolean; source?: ReviewCommentSource }) {
  const sink = useContext(ReviewDraft), noteId = useId();
  const [pending, setPending] = useState<{ index: number; source: ReviewCommentSource; threadId: string }>();
  const admitted = useRef<object>(), commentButtons = useRef(new Map<number, HTMLButtonElement>());
  const [commentIndex, setCommentIndex] = useState<number>();
  const targets = useMemo(() => lines.flatMap((line, index) => line.oldNumber !== undefined || line.newNumber !== undefined ? [index] : []), [lines]);
  const [note, setNote] = useState(""), [notice, setNotice] = useState("");
  const positions = useMemo(() => new Map(lines.map((line, index) => [line, index])), [lines]);
  const currentSource = !!pending && source?.identity === pending.source.identity && sink?.threadId === pending.threadId;
  const canHighlight = lines.reduce((total, line) => total + line.text.length, 0) <= 200000;
  const tokens = useMemo(() => new Map(lines.map(line => [line, code(line.text, path, canHighlight)])), [lines, path, canHighlight]);
  const render = (line: DiffLine, key: string) => {
    const index = positions.get(line)!, commentable = !!sink && !!source && (line.oldNumber !== undefined || line.newNumber !== undefined);
    const excerpt = pending?.index === index ? reviewExcerpt(line.text) : undefined;
    return <Fragment key={key}><div className={`review-code-line is-${line.kind}`}>
      {commentable && <button type="button" className="review-comment-trigger" tabIndex={index === (commentIndex ?? targets[0]) ? 0 : -1} aria-keyshortcuts="ArrowDown ArrowUp Home End" aria-describedby={`${noteId}-keys`}
        ref={button => { if (button) commentButtons.current.set(index, button); else commentButtons.current.delete(index); }} onFocus={() => setCommentIndex(index)} onKeyDown={event => {
          const visible = targets.filter(value => commentButtons.current.has(value)), position = visible.indexOf(index), next = event.key === "ArrowDown" ? visible[Math.min(position + 1, visible.length - 1)] : event.key === "ArrowUp" ? visible[Math.max(0, position - 1)] : event.key === "Home" ? visible[0] : event.key === "End" ? visible.at(-1) : undefined;
          if (next === undefined) return; event.preventDefault(); setCommentIndex(next); commentButtons.current.get(next)?.focus();
        }} aria-label={`Comment on ${path}, ${reviewLineLabel(line)}`} title="Comment on this recorded line" onClick={() => { setPending({ index, source: source!, threadId: sink!.threadId }); setNote(""); setNotice(""); }}>+</button>}
      <span className="review-code-number" aria-hidden="true">{line.num || (commentable ? line.oldNumber : "")}</span><span className="review-code-marker" aria-hidden="true">{line.marker}</span><span className="review-code-content">{tokens.get(line)}</span></div>
      {pending?.index === index && <form className="review-comment-form" aria-label={`Review comment on ${path}, ${reviewLineLabel(line)}`} onSubmit={event => {
        event.preventDefault(); if (!currentSource || !sink?.ready || !note.trim() || admitted.current === pending) return;
        const failure = sink.append(reviewCommentText(path, pending.source, line, note));
        if (failure) setNotice(failure); else { admitted.current = pending; setPending(undefined); setNote(""); setNotice("Comment added as editable draft text. Review or remove it before sending."); }
      }}>
        <p>{path} · {reviewLineLabel(line)} · recorded excerpt</p><p>{pending.source.label}</p><pre>{excerpt!.text}{excerpt!.shortened ? "… [excerpt shortened]" : ""}</pre><label htmlFor={noteId}>Review note</label>
        <textarea id={noteId} value={note} maxLength={2000} onChange={event => setNote(event.target.value.slice(0, 2000))} rows={3} />
        {!currentSource && <p role="status">This comparison or conversation changed. Choose the line again.</p>}
        {currentSource && !sink?.ready && <p role="status">{sink?.reason || "Wait for the main draft to be ready."}</p>}
        <div><button type="submit" disabled={!currentSource || !sink?.ready || !note.trim()}>Add to draft</button><button type="button" onClick={() => { setPending(undefined); setNote(""); setNotice(""); }}>Cancel comment</button></div>
      </form>}</Fragment>;
  };
  const groups: ReactNode[] = []; let previousEnd = 1;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (line.kind === "ctx") { const context = [line], start = i; while (lines[i + 1]?.kind === "ctx") context.push(lines[++i]!); groups.push(<Context key={`context-${start}`} lines={context} path={path} render={render} onFold={() => setCommentIndex(undefined)} />); }
    else if (line.kind === "hunk") {
      const match = /^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@/.exec(line.text);
      if (match) { const start = Number(match[1]), gap = Math.max(0, start - previousEnd); groups.push(<div key={i} className="review-hunk" title={line.text}>{gap ? `${gap} lines not included in this diff` : line.text}</div>); previousEnd = start + Number(match[2] ?? 1); }
      else groups.push(<div key={i} className="review-hunk">{line.text}</div>);
    } else groups.push(render(line, String(i)));
  }
  return <div className={`review-code mono${wrap ? " is-wrapped" : ""}`} role="region" aria-label={`Recorded diff ${path}`} tabIndex={0}>{sink && source && <span id={`${noteId}-keys`} className="sr-only">Use Up and Down to choose a comment line, Home or End for the first or last line, and Enter to comment.</span>}{groups}{notice && <p role="status" className="review-explanation">{notice}</p>}</div>;
}, (before, after) => before.lines === after.lines && before.path === after.path && before.wrap === after.wrap
  && before.source?.identity === after.source?.identity && before.source?.label === after.source?.label);
