import { Icon } from "../components/Icon";
import { fileIcon } from "./fileIcons";
import { GitStatusBadge, GIT_STATUS_LABEL } from "./GitChangesTool";
import type { GitReviewModel } from "./useGitChanges";
export function GitComparisonCaption({ model }: { model: GitReviewModel }) {
  const result = model.result?.state === "ready" ? model.result : undefined;
  if (!result) return <p className="desktop-change-evidence">{model.loading ? "Git comparison is running." : model.error || (model.result?.state === "unavailable" ? model.result.reason : "Git status has not been checked.")}</p>;
  const minutes = model.capturedAt === undefined ? undefined : Math.max(0, Math.floor((Date.now() - model.capturedAt) / 60000));
  return <div className="desktop-change-evidence"><p>Cached Git comparison{minutes === undefined ? "" : minutes === 0 ? " · checked just now" : ` · checked ${minutes} min ago`}{result.head ? ` · HEAD ${result.head.slice(0, 8)}` : " · no HEAD commit"}</p>
    <details><summary>{result.partial ? "Partial raw-byte comparison" : "Raw-byte comparison"}{result.uncompared.length ? ` · ${result.uncompared.length} not compared` : ""}</summary>{result.warnings.map(warning => <p key={warning}>{warning}</p>)}<p>Badges describe this captured comparison. Open Git changes to refresh.</p></details></div>;
}
/** Only renders evidence already requested by the user; never starts an inspection. */
export function GitNavigatorChanges({ model, review }: { model: GitReviewModel; review?: (path?: string) => void }) {
  const result = model.result?.state === "ready" ? model.result : undefined;
  if (!result) return <div><p className="desktop-change-evidence">{model.loading ? "Comparing working tree…" : "No cached Git changes to show."}</p><button type="button" disabled={!review} onClick={() => review?.()}>Open Git changes</button></div>;
  return <>{result.changes.length ? <ul>{result.changes.slice(0, 500).map(change => <li key={change.path}>
    <button type="button" className="desktop-file-row" aria-label={`${change.path} ${GIT_STATUS_LABEL[change.status]}`} disabled={!review} aria-describedby={change.from ? `git-rename-${encodeURIComponent(change.path)}` : undefined} title={change.from ? `${change.from} → ${change.path}` : change.path} onClick={() => review?.(change.path)}>
      <Icon name={fileIcon(change.path)} size={14} /><span className="desktop-git-path"><span>{change.path.split("/").at(-1)}</span>{change.path.includes("/") && <small>{change.path.slice(0, change.path.lastIndexOf("/"))}</small>}</span><GitStatusBadge status={change.status} />
    </button>{change.from && <span id={`git-rename-${encodeURIComponent(change.path)}`} className="sr-only">Renamed from {change.from}</span>}
  </li>)}</ul> : <p className="desktop-change-evidence">{result.partial ? "No changes were confirmed in the inspected subset." : "No uncommitted changes were found in this comparison."}</p>}
    {result.changes.length > 500 && <p>Showing the first 500 changed paths.</p>}
  </>;
}
