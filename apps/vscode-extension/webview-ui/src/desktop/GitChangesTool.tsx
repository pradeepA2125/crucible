import type { ReviewCommentSource } from "../drafts/reviewDraft";
import type { ReactNode } from "react";
import { Icon } from "../components/Icon";
import { DiffLines } from "../components/shared/DiffPanes";
import { fileIcon } from "./fileIcons";
import { useReviewViewport } from "./useReviewViewport";
import { ReviewFileMenu } from "./ReviewFileMenu";
import { ReviewSurface } from "./ReviewSurface";
import { ReviewCounts } from "./ChangedFilesTree";
import type { GitStatus } from "../../../../desktop/src/workspace-git";
import type { GitReviewModel } from "./useGitChanges";
export const GIT_STATUS_LABEL: Record<GitStatus, string> = { A: "Added", M: "Modified", D: "Deleted", R: "Renamed", U: "Untracked" };
export function GitStatusBadge({ status }: { status: GitStatus }) {
  return <small className="desktop-git-status" data-status={status} title={GIT_STATUS_LABEL[status]} aria-label={GIT_STATUS_LABEL[status]}>{status}</small>;
}
export interface ComparisonPresentation {
  commentSource?(path:string):ReviewCommentSource|undefined;
  title:string; referenceControl:ReactNode; refreshLabel:string; notice:ReactNode; notes:ReactNode; summary:string;
  loadingText:string; idleText:string; idleInformation:string; unavailableInformation:string; emptyInformation:string; emptyText:string; partialEmptyText:string; allFilesText:string; fileNote(path:string):ReactNode;
}
export function GitChangesTool({ model, scope, presentation }: { model: GitReviewModel; scope?: ReactNode; presentation?: ComparisonPresentation }) {
  const result = model.result?.state === "ready" ? model.result : undefined, current = result?.changes.find(change => change.path === model.selected);
  const age = model.capturedAt === undefined ? undefined : Math.max(0, Math.floor((Date.now() - model.capturedAt) / 60000));
  const entries = result?.changes ?? [], rows = entries.slice(0, 500).map(change => {
    const counts = model.known.get(change.path), stateId = `git-viewed-state-${encodeURIComponent(change.path)}`;
    return { path: change.path, status: GIT_STATUS_LABEL[change.status], description: change.from ? `Renamed from ${change.from}` : undefined,
      counts: counts && counts.generation === result?.generation && counts.additions !== undefined ? { additions: counts.additions, deletions: counts.deletions ?? 0 } : undefined,
      viewed: model.isViewed(change.path), earlier: model.needsRecheck(change.path), review: <label className="review-tree-viewed" title={model.needsRecheck(change.path) ? "Viewed earlier · open to confirm" : "Viewed"}>
        <input type="checkbox" aria-label={"Viewed " + change.path} aria-describedby={!model.canView(change.path) ? stateId : undefined} disabled={model.loading || !model.canView(change.path)} checked={model.isViewed(change.path)} onChange={event => model.setViewed(change.path, event.target.checked)} />
        {model.needsRecheck(change.path) && <span className="sr-only">Viewed earlier</span>}
        {!model.canView(change.path) && <span id={stateId} className="sr-only">{model.needsRecheck(change.path) ? "Viewed earlier. Open this file to confirm." : "Open this file to inspect a complete diff before marking it viewed."}</span>}
      </label> };
  });
  const allFiles = !!result && !result.partial && entries.length <= 50 && !model.large && !!model.load && !!model.details;
  const view = useReviewViewport({ enabled: allFiles, identity: result?.generation ?? "", paths: rows.map(row => row.path), selected: current?.path, choose: model.select, highlight: model.highlight, load: model.load, ready: path => !!model.details?.get(path) });
  const shown = allFiles ? entries : current ? [current] : [];
  const summary = result ? entries.length + (entries.length === 1 ? " file · " : " files · ") + [...model.known.keys()].filter(path => model.isViewed(path)).length + " viewed" + (model.pendingViewed ? ` · ${model.pendingViewed} to recheck` : "") : "Inspect this project's working tree.";
  return <ReviewSurface title={presentation?.title ?? "Uncommitted vs HEAD"} scope={scope} referenceControl={presentation?.referenceControl} refreshLabel={presentation?.refreshLabel} notice={presentation?.notice} reference={result ? result.head ? "HEAD " + result.head.slice(0, 8) : "No HEAD commit" : "HEAD"} summary={presentation?.summary ?? summary} files={rows} selected={current?.path} select={view.jump} viewport={view.viewport} scrolled={view.scrolled} disabled={model.loading} refresh={() => void model.refresh()}
    information={model.loading ? presentation?.loadingText ?? "Comparing working tree…"
      : model.error || model.result?.state === "unavailable" ? presentation?.unavailableInformation ?? "Git comparison unavailable."
      : !model.result ? presentation?.idleInformation ?? "Ready to compare the working tree."
      : result && !entries.length ? presentation?.emptyInformation ?? (result.partial ? "Partial comparison · no changes confirmed" : "Working tree comparison complete.")
      : result?.partial ? "Partial comparison · showing one file at a time"
      : allFiles ? presentation?.allFilesText ?? "Uncommitted changes · scroll to review all files"
      : model.large || entries.length > 50 ? "This diff is large, showing one file at a time" : "Uncommitted changes · showing one file at a time"}
    notes={presentation?.notes ?? <>{entries.length > 500 && <p role="status">Showing the first 500 changed files.</p>}{age !== undefined && <p>Checked {age === 0 ? "just now" : age + " min ago"}{result?.head ? " · HEAD " + result.head.slice(0, 8) : result ? " · no HEAD commit" : ""}</p>}<p>Counts appear per file after its diff is inspected; no whole-project line total is inferred.</p>
      {result?.warnings.map(warning => <p key={warning}>{warning}</p>)}{!!result?.uncompared.length && <details><summary>{result.uncompared.length} {result.uncompared.length === 1 ? "file" : "files"} not compared</summary><ul>{result.uncompared.slice(0, 50).map(entry => <li key={entry.path}>{entry.path} — {entry.reason}</li>)}</ul>{result.uncompared.length > 50 && <p>Showing the first 50 paths.</p>}</details>}</>}>
    {wrap => <>
      {model.loading && <p role="status" className="desktop-empty">{presentation?.loadingText ?? "Comparing working tree…"}</p>}{model.error && <p role="status" className="desktop-empty">{model.error}</p>}
      {model.result?.state === "unavailable" && <p role="status" className="desktop-empty">{model.result.reason}</p>}
      {!model.result && !model.loading && !model.error && <p className="desktop-empty">{presentation?.idleText ?? "Refresh to compare this project."}</p>}
      {result && !entries.length && <p className="desktop-empty">{result.partial ? presentation?.partialEmptyText ?? "No changes were confirmed in the inspected subset." : presentation?.emptyText ?? "No uncommitted changes were found."}</p>}
      {shown.map(current => { const detail = model.details?.get(current.path) ?? (model.detail?.path === current.path ? model.detail : undefined), diff = detail?.result, selectedCounts = rows.find(row => row.path === current.path)?.counts;
        return <article key={current.path} ref={element => view.section(current.path, element)} data-review-path={current.path} aria-label={"Changes to " + current.path} className={`review-file-detail${wrap ? " is-wrapped" : ""}`}>
        <header className="review-file-header"><Icon name={fileIcon(current.path)} size={16} /><span className="review-file-path" title={current.path}><bdi>{current.path}</bdi></span>{selectedCounts && <ReviewCounts {...selectedCounts} />}
          <button type="button" className={`desktop-icon${model.isViewed(current.path) ? " is-active" : ""}`} aria-label="Mark current file viewed" aria-pressed={model.isViewed(current.path)} aria-describedby={!model.canView(current.path) ? `git-current-viewed-state-${encodeURIComponent(current.path)}` : undefined} disabled={!model.canView(current.path) || model.loading} title={model.canView(current.path) ? "Mark current file viewed" : "Inspect a complete diff first"} onClick={() => model.setViewed(current.path, !model.isViewed(current.path))}><Icon name="check" size={15} /></button>
          {model.openFile && current.status !== "D" && <button type="button" className="desktop-icon" aria-label="Open file" title="Open current file" onClick={() => model.openFile!(current.path)}><Icon name="expand" size={14} /></button>}<ReviewFileMenu path={current.path} open={model.openFile && current.status !== "D" ? () => model.openFile!(current.path) : undefined} /></header>
        {!model.canView(current.path) && <span id={`git-current-viewed-state-${encodeURIComponent(current.path)}`} className="sr-only">{model.needsRecheck(current.path) ? "Viewed earlier. Open this file to confirm." : "Inspect a complete diff before marking this file viewed."}</span>}
        {presentation?.fileNote(current.path)}
        {current.from && <p className="review-explanation">Renamed from {current.from}</p>}{current.note && <p className="review-explanation">{current.note === "attributes" ? "Attribute rules may affect this comparison." : "Content is too large to compare."}</p>}
        {!detail && <p className="desktop-empty">Scroll to load this diff. <button type="button" onClick={() => model.select(current.path)}>Load diff for {current.path}</button></p>}{detail?.loading && <p role="status" className="desktop-empty">Loading diff…</p>}{detail?.error && <p role="status" className="desktop-empty">{detail.error} <button type="button" onClick={() => model.select(current.path)}>Retry diff for {current.path}</button></p>}
        {diff && <>{("mode" in diff && diff.mode) && <p className="review-explanation">Mode {diff.mode.from} → {diff.mode.to}</p>}{("type" in diff && diff.type) && <p className="review-explanation">Type {diff.type.from} → {diff.type.to}</p>}
          {diff.kind === "text" ? <>{diff.unified ? <DiffLines diff={diff.unified} presentation="review" path={current.path} wrap={wrap} source={presentation ? (() => { const source = presentation.commentSource?.(current.path); return source && { ...source, identity: JSON.stringify([source.identity, diff.fingerprint]) }; })() : { identity: JSON.stringify([result!.generation, current.path, diff.fingerprint]), label: `Uncommitted vs ${result!.head ? `HEAD ${result!.head}` : "no HEAD commit"}${model.capturedAt === undefined ? "" : ` · compared at ${new Date(model.capturedAt).toLocaleString()}`}` }} /> : <p className="desktop-empty">No text hunks were produced; raw bytes or file properties can still differ.</p>}{diff.truncated && <p role="note" className="review-explanation">Only a bounded portion of this diff is shown.</p>}</>
            : <p className="desktop-empty">{diff.kind === "binary" ? "Binary or unsupported-encoding content; inspect the file properties." : diff.kind === "too-large" ? "This content is too large to compare." : diff.reason}</p>}</>}
      </article>; })}
    </>}
  </ReviewSurface>;
}
