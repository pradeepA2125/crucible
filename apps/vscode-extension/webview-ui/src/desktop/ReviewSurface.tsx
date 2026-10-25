import { useCallback, useState, type RefObject, type ReactNode } from "react";
import { Icon } from "../components/Icon";
import { ChangedFilesTree, ReviewCounts, type ReviewFile } from "./ChangedFilesTree";
interface Props {
  viewport?: RefObject<HTMLDivElement>; scrolled?(): void;
  referenceControl?: ReactNode; refreshLabel?: string; notice?: ReactNode;
  scope?: ReactNode; title: string; reference?: string; summary: string; counts?: { additions: number; deletions: number }; countsLabel?: string;
  files: ReviewFile[]; selected?: string; select(path: string): void; disabled?: boolean;
  refresh?: () => void; information: string; notes?: ReactNode; children(wrap: boolean): ReactNode;
}
/** One shared review frame: comparison pills, selected diff, independently scrollable tree. */
export function ReviewSurface({ referenceControl, refreshLabel = "Refresh Git comparison", notice, viewport, scrolled, scope, title, reference, summary, counts, countsLabel, files, selected, select, disabled, refresh, information, notes, children }: Props) {
  const [navigator, setNavigator] = useState(true), [wrap, setWrap] = useState(false);
  const [visiblePaths, setVisiblePaths] = useState<string[]>(files.map(file => file.path));
  const receiveOrder = useCallback((paths: string[]) => setVisiblePaths(previous => JSON.stringify(previous) === JSON.stringify(paths) ? previous : paths), []);
  const visible = visiblePaths.filter(path => files.some(file => file.path === path));
  const outside = !!selected && navigator && !visible.includes(selected), ordered = navigator && !outside ? visible : files.map(file => file.path);
  const index = ordered.indexOf(selected ?? ""), position = files.findIndex(file => file.path === selected);
  return <section role="region" aria-label="Changes" className="review-surface">
    <div className="review-toolbar">
      <div className="review-comparison-pill">{scope ?? <span>{title}</span>}{counts && <span title={countsLabel} aria-label={countsLabel}>{countsLabel?.startsWith("Inspected") && <small className="review-inspected">Inspected </small>}<ReviewCounts {...counts} /></span>}</div>
      {referenceControl ? <div className="review-reference is-control">{referenceControl}</div> : reference && <span className="review-reference" title={reference}>{reference}</span>}
      <div className="review-tools">
        {refresh && <button type="button" className="desktop-icon" aria-label={refreshLabel} title="Refresh comparison" disabled={disabled} onClick={refresh}><Icon name="retry" size={15} /></button>}
        <button type="button" className={`desktop-icon${wrap ? " is-active" : ""}`} aria-label="Wrap diff lines" aria-pressed={wrap} title="Wrap diff lines" onClick={() => setWrap(!wrap)}><Icon name="wrap" size={15} /></button>
        <button type="button" className={`desktop-icon${navigator ? " is-active" : ""}`} aria-label="Toggle changed files" aria-expanded={navigator} title="Toggle changed files" onClick={() => setNavigator(!navigator)}><Icon name="folder" size={16} /></button>
      </div>
    </div>
    <div className="review-banner"><Icon name="info" size={15} /><span>{information}</span><nav aria-label="Changed file navigation">
      <button type="button" className="desktop-icon" aria-label="Previous changed file" title="Previous changed file" disabled={disabled || index <= 0} onClick={() => ordered[index - 1] && select(ordered[index - 1]!)}><Icon name="chev-l" size={15} /></button>
      <button type="button" className="desktop-icon" aria-label="Next changed file" title="Next changed file" disabled={disabled || !ordered.length || index >= ordered.length - 1} onClick={() => ordered[Math.max(0, index + 1)] && select(ordered[Math.max(0, index + 1)]!)}><Icon name="chev-r" size={15} /></button>
    </nav></div>
    <div className="review-metadata"><span>{summary}</span>{selected && <span role="status" className="review-position">File {position + 1} of {files.length}: {selected}{outside ? " · outside the shown tree; navigation uses all listed files" : ""}</span>}{notes && <details><summary>Comparison details</summary><div>{notes}</div></details>}</div>
    {notice && <p className="review-explanation review-standing-note">{notice}</p>}
    <div className={`review-columns${navigator ? " has-navigator" : ""}`}>
      <div ref={viewport} onScroll={scrolled} className="review-diff-body">{children(wrap)}</div>
      {navigator && <ChangedFilesTree onOrder={receiveOrder} files={files} selected={selected} select={select} disabled={disabled} />}
    </div>
  </section>;
}
