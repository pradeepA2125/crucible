import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "../components/Icon";
import { DiffLines } from "../components/shared/DiffPanes";
import { fileIcon } from "./fileIcons";
import { useReviewViewport } from "./useReviewViewport";
import { ReviewFileMenu } from "./ReviewFileMenu";
import { ReviewSurface } from "./ReviewSurface";
import { ReviewCounts } from "./ChangedFilesTree";
import type { ConversationChange, ConversationChanges, RecordedEditTarget } from "./conversationChanges";
export interface ExactEditSelection extends RecordedEditTarget { request: number }
export interface ChangesModel {
  changes: ConversationChanges; selected?: string; select(path: string): void;
  target?: ExactEditSelection; highlight?(path: string): void;
  isViewed(change: ConversationChange): boolean; setViewed(change: ConversationChange, viewed: boolean): void;
  openFile?(path: string): void;
}
/** Read-only applied records in the same review frame as Git; gates remain in the transcript. */
export function ChangesTool({ model, scope }: { model: ChangesModel; scope?: ReactNode }) {
  const { files, omitted } = model.changes, current = model.selected === undefined ? files[0] : files.find(change => change.path === model.selected);
  const target = model.target, targetFile = target && files.find(file => file.path === target.path);
  // Re-match current evidence on every render. Rewind can reuse the same ordinal with different bytes.
  const targetIndex = targetFile ? targetFile.edits.findIndex(edit => edit.key === target!.key && edit.signature === target!.signature) : -1;
  const targetAvailable = !target || targetIndex >= 0;
  const selectedEdit = targetFile?.edits[targetIndex];
  // A later live edit can change this turn's total or make this no longer its last edit.
  const turnEdits = selectedEdit ? targetFile!.edits.filter(edit => edit.turn === selectedEdit.turn) : [];
  const lastInTurn = turnEdits[turnEdits.length - 1]?.key === selectedEdit?.key;
  const [pagination, setPagination] = useState<{ basis: string; page: number }>();
  const basis = JSON.stringify([current?.path, current?.signature, target?.request, target?.key, target?.signature]);
  const targetPage = targetAvailable && current?.path === target?.path ? Math.floor(targetIndex / 5) : 0;
  const pageCount = Math.max(1, Math.ceil((current?.edits.length ?? 0) / 5));
  const totalLines = files.reduce((total, file) => total + file.edits.reduce((sum, edit) => sum + (edit.diff?.split("\n").length ?? 0) + 2, 0), 0);
  const totalBytes = files.reduce((total, file) => total + file.edits.reduce((sum, edit) => sum + (edit.diff?.length ?? 0) * 2, 0), 0);
  const allFiles = files.length <= 50 && !omitted && totalLines <= 3000 && totalBytes <= 8 * 1024 * 1024;
  const activePage = allFiles ? 0 : Math.max(0, Math.min(pagination?.basis === basis ? pagination.page : targetPage, pageCount - 1));
  const setPage = (page: number) => setPagination({ basis, page });
  const identity = files.map(file => file.signature).join(":");
  const view = useReviewViewport({ enabled: allFiles, identity, paths: files.map(file => file.path), selected: current?.path, choose: model.select, highlight: model.highlight });
  const recordElements = useRef(new Map<string, HTMLElement>()), scrolledRequest = useRef<string>();
  const scrollRequest = target && JSON.stringify([target.request, target.path, target.key, target.signature]);
  // Declared after useReviewViewport, so its initial file alignment happens first.
  // Never scroll the document or move focus; offset the sticky file header in this viewport.
  useEffect(() => {
    if (!target || !targetAvailable || scrolledRequest.current === scrollRequest) return;
    const root = view.viewport.current, element = recordElements.current.get(JSON.stringify([target.path, target.key]));
    if (!root || !element) return;
    const header = element.closest("article")?.querySelector(".review-file-header");
    scrolledRequest.current = scrollRequest;
    root.scrollTop = Math.max(0, root.scrollTop + element.getBoundingClientRect().top - root.getBoundingClientRect().top - (header?.getBoundingClientRect().height ?? 0) - 8);
  }, [target?.request, target?.path, target?.key, target?.signature, targetAvailable, activePage, allFiles, identity, scrollRequest]);
  const shown = !targetAvailable ? [] : allFiles ? files : current ? [current] : [];
  const viewed = files.filter(change => model.isViewed(change)).length;
  const rows = files.map(change => ({ path: change.path, counts: { additions: change.additions, deletions: change.deletions }, viewed: model.isViewed(change), review: <label className="review-tree-viewed" title="Mark recorded edits viewed"><input type="checkbox" aria-label={`Viewed ${change.path}`} checked={model.isViewed(change)} onChange={event => model.setViewed(change, event.target.checked)} /><span className="sr-only">Viewed</span></label> }));
  const counts = files.reduce((total, file) => ({ additions: total.additions + file.additions, deletions: total.deletions + file.deletions }), { additions: 0, deletions: 0 });
  return <ReviewSurface title="Conversation edits" scope={scope} reference="Applied edits" summary={files.length ? `${files.length} ${files.length === 1 ? "file" : "files"} · ${viewed} viewed` : "Edits applied in this conversation appear here."} counts={files.length ? counts : undefined} countsLabel={omitted ? "Totals for listed recorded files" : "Recorded additions and deletions"} files={rows} selected={current?.path} select={view.jump} viewport={view.viewport} scrolled={view.scrolled}
    information={files.length ? allFiles ? "Recorded conversation edits · scroll to review all files" : "This diff is large, showing one file at a time" : "Applied conversation edits appear here"}
    notice={target && targetAvailable ? <span role="status">{lastInTurn ? "Last edit" : "Recorded edit"} to {target.path} in this turn · edit {targetIndex + 1} of {targetFile!.edits.length}.{turnEdits.length > 1 && ` This turn made ${turnEdits.length} edits to this file; the transcript counts cover the turn total.`}</span> : undefined}
    notes={<><p>Each diff is as recorded when the edit was applied.</p>{omitted > 0 && <p role="status">{omitted.toLocaleString()} more changed {omitted === 1 ? "file is" : "files are"} not listed.</p>}</>}>
    {wrap => <>
      {!targetAvailable && <div role="status" className="desktop-empty"><p>This recorded edit is unavailable in this conversation. It may have changed or been removed.</p>{targetFile && <button type="button" onClick={() => model.select(targetFile.path)}>Review available edits to {targetFile.path}</button>}</div>}
      {!target && model.selected !== undefined && !current && <p role="status" className="desktop-empty">Recorded changes for {model.selected} are unavailable in this conversation.</p>}
      {!files.length && !target && <p className="desktop-empty">No edits have been applied in this conversation.</p>}
      {shown.map(current => <article key={current.path} ref={element => view.section(current.path, element)} data-review-path={current.path} aria-label={`Changes to ${current.path}`} className={`review-file-detail${wrap ? " is-wrapped" : ""}`}>
        <header className="review-file-header"><Icon name={fileIcon(current.path)} size={16} /><span className="review-file-path" title={current.path}><bdi>{current.path}</bdi></span><ReviewCounts additions={current.additions} deletions={current.deletions} />
          <button type="button" className={`desktop-icon${model.isViewed(current) ? " is-active" : ""}`} aria-label="Mark current file viewed" aria-pressed={model.isViewed(current)} title="Mark current file viewed" onClick={() => model.setViewed(current, !model.isViewed(current))}><Icon name="check" size={15} /></button>
          {model.openFile && <button type="button" className="desktop-icon" aria-label="Open file" title="Open current file" onClick={() => model.openFile!(current.path)}><Icon name="expand" size={14} /></button>}<ReviewFileMenu path={current.path} open={model.openFile ? () => model.openFile!(current.path) : undefined} /></header>
        {!allFiles && pageCount > 1 && <nav aria-label="Recorded edits pages" className="desktop-source-pages"><button type="button" disabled={activePage === 0} onClick={() => setPage(activePage - 1)}>Previous edits</button><span>Edits {activePage * 5 + 1}–{Math.min((activePage + 1) * 5, current.edits.length)} of {current.edits.length}</span><button type="button" disabled={activePage + 1 === pageCount} onClick={() => setPage(activePage + 1)}>Next edits</button></nav>}
        {(allFiles ? current.edits : current.edits.slice(activePage * 5, (activePage + 1) * 5)).map((edit, offset) => { const index = allFiles ? offset : activePage * 5 + offset; return <section key={edit.key} ref={element => { const key = JSON.stringify([current.path, edit.key]); if (element) recordElements.current.set(key, element); else recordElements.current.delete(key); }} aria-label={`Edit ${index + 1} of ${current.edits.length}`} aria-current={target && current.path === target.path && edit.key === target.key && edit.signature === target.signature ? "true" : undefined} className="review-recorded-edit">
          {current.edits.length > 1 && <h3>{edit.turn > 0 ? `Turn ${edit.turn} · ` : ""}Edit {index + 1} of {current.edits.length}<ReviewCounts additions={edit.additions} deletions={edit.deletions} /></h3>}
          {edit.diff === undefined ? <p className="desktop-empty">No diff text was recorded for this edit.</p> : edit.diff.trim() === "" ? <p className="desktop-empty">No line changes were recorded for this edit.</p> : <DiffLines diff={edit.diff} presentation="review" path={current.path} wrap={wrap} source={{ identity: JSON.stringify([current.path, edit.key, edit.signature]), label: `Conversation edits · ${edit.turn > 0 ? `turn ${edit.turn} · ` : ""}edit ${index + 1} of ${current.edits.length}` }} />}
          {edit.truncated && <p role="note" className="review-explanation">Only a bounded portion of this recorded diff is shown. Open the file to see its current content.</p>}
        </section>; })}
      </article>)}
    </>}
  </ReviewSurface>;
}
