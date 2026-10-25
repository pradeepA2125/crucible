import { CardShell } from "../components/shared/CardShell";
import { BtnGhost,BtnPrimary } from "../components/shared/buttons";
import { useEffect, useMemo, useState } from "react";
import type { GitReviewModel } from "./useGitChanges";
import { GitStatusBadge } from "./GitChangesTool";
import { GitComparisonCaption, GitNavigatorChanges } from "./GitNavigatorChanges";
import type { BrowserEntry, BrowserSearch } from "../../../../desktop/src/workspace-browser";
import type { ProjectFiles } from "./useProjectFiles";
import { FileRequestCancelled } from "./workspaceClient";
import { LocalImage, WorkspaceDocument } from "./WorkspaceDocument";
import { FileToolbar } from "./FileToolbar";
import { SourcePreview } from "./SourcePreview";
import { fileIcon } from "./fileIcons";
import { Icon } from "../components/Icon";
export function WorkspaceNavigator({ model, project = "Workspace", git, reviewGit }: { model: ProjectFiles; project?: string; git?: GitReviewModel; reviewGit?: (path?: string) => void }) {
  const [changedScope, setChangedScope] = useState<"conversation" | "git">("conversation");
  const gitResult = git?.result?.state === "ready" ? git.result : undefined;
  const gitStatuses = useMemo(() => new Map(gitResult?.changes.map(change => [change.path, change.status])), [gitResult]);
  const [allOpen, setAllOpen] = useState(true), [changesOpen, setChangesOpen] = useState(true), [expandedChanges, setExpandedChanges] = useState<Set<string>>(() => new Set());
  const changedPaths = [...new Set([...model.changed, ...model.tabs.filter(tab => tab.stale).map(tab => tab.path)])].filter(path => !!path && path.length <= 4096 && path.split("/").length <= 128 && !/^[\\/]/.test(path) && !/[\\\x00-\x1f\x7f:]/.test(path) && path.split("/").every(part => part && part !== "." && part !== ".."));
  const containsChange = (path: string) => changedPaths.some(changed => changed === path || changed.startsWith(`${path}/`));
  type ChangedNode = { path: string; name: string; children: Map<string, ChangedNode>; };
  const changedTree = new Map<string, ChangedNode>();
  for (const path of changedPaths.slice(0, 100)) {
    let nodes = changedTree, prefix = "";
    for (const name of path.split("/")) { prefix = prefix ? `${prefix}/${name}` : name; if (!nodes.has(name)) nodes.set(name, { path: prefix, name, children: new Map() }); const node = nodes.get(name)!; nodes = node.children; }
  }
  const changeRows = (nodes: Map<string, ChangedNode>, depth = 0): React.ReactNode => <ul>{[...nodes.values()].map(node => <li key={node.path}>
    <button type="button" className="desktop-file-row" style={{ paddingLeft: 8 + depth * 14 }} title={node.children.size ? "Contains changed files" : model.changed.includes(node.path) ? "Applied in this conversation" : "Preview changed"} aria-describedby={!node.children.size ? `changed-evidence-${encodeURIComponent(node.path)}` : undefined} aria-expanded={node.children.size ? expandedChanges.has(node.path) : undefined} onClick={() => {
      if (!node.children.size) model.open(node.path);
      else setExpandedChanges(value => { const next = new Set(value); if (next.has(node.path)) next.delete(node.path); else next.add(node.path); return next; });
    }}><Icon name={node.children.size ? expandedChanges.has(node.path) ? "chev-d" : "chev-r" : fileIcon(node.path)} size={14} /><span>{node.name}</span>{!node.children.size && <small className="desktop-file-badge">{model.changed.includes(node.path) ? "Edited" : "Preview"}</small>}<span className="desktop-change-dot" role="img" aria-label="Changed" /></button>{!node.children.size && <span id={`changed-evidence-${encodeURIComponent(node.path)}`} className="sr-only">{model.changed.includes(node.path) ? "Applied in this conversation" : "Preview changed on disk or became unavailable"}</span>}
    {!!node.children.size && expandedChanges.has(node.path) && changeRows(node.children, depth + 1)}
  </li>)}</ul>;
  const [query, setQuery] = useState(""), [includeIgnored, setIncludeIgnored] = useState(false);
  const [search, setSearch] = useState<BrowserSearch>(), [error, setError] = useState(""), [loading, setLoading] = useState(false);
  useEffect(() => {
    let live = true; setSearch(undefined); setError(""); model.port?.cancelSearch(); setLoading(!!query.trim());
    const timer = setTimeout(() => {
      if (!query.trim() || !model.port) return;
      void model.port.search(query, includeIgnored).then(result => { if (live) setSearch(result); }, reason => { if (live && !(reason instanceof FileRequestCancelled)) setError(reason instanceof Error ? reason.message : "Search unavailable."); }).finally(() => { if (live) setLoading(false); });
    }, 180);
    return () => { live = false; clearTimeout(timer); model.port?.cancelSearch(); };
  }, [query, includeIgnored, model.port]);
  const row = (entry: BrowserEntry, depth: number) => {
    const directory = entry.kind === "directory", expanded = model.expanded.has(entry.path);
    const disabled = entry.unsupportedName || !["directory", "file"].includes(entry.kind);
    const detail = entry.unsupportedName ? "Name not supported here" : entry.kind === "symlink" ? `Link ${entry.targetLocation === "inside" ? `→ ${entry.targetPath || "inside this project"}` : entry.targetLocation === "outside" ? "outside the project" : entry.targetLocation === "protected" ? "to protected content" : "target unavailable"}; links are not followed` : entry.kind === "protected" ? "Internal content is protected" : entry.kind === "special" ? "Special file cannot be previewed" : entry.kind === "unavailable" ? "Path unavailable; refresh" : entry.ignoreStatus === "ignored" ? "Ignored by local pattern" : entry.ignoreStatus === "unknown" ? "Ignore status unknown" : entry.path;
    const detailId = `workspace-detail-${depth}-${encodeURIComponent(entry.path)}`;
    return <li key={entry.path}>
      <button type="button" aria-disabled={!!disabled} aria-describedby={disabled ? detailId : undefined} className={`desktop-file-row${entry.ignoreStatus === "ignored" ? " is-ignored" : ""}`} style={{ paddingLeft: 8 + depth * 14 }} title={detail} aria-current={model.selection === entry.path ? "true" : undefined} aria-expanded={directory ? expanded : undefined}
        onClick={() => { if (!disabled) { if (directory) model.toggleDirectory(entry.path); else model.open(entry.path); } }}>
        <Icon name={directory ? expanded ? "chev-d" : "chev-r" : fileIcon(entry.path)} size={14} />{directory && <Icon name="folder" size={14} />}<span>{entry.name}</span>
        {disabled && <small className="desktop-file-badge">{entry.unsupportedName ? "Unsupported" : entry.kind === "protected" ? "Protected" : entry.kind === "symlink" ? "Link" : entry.kind === "special" ? "Special" : "Unavailable"}</small>}
        {!directory && gitStatuses.has(entry.path) && <GitStatusBadge status={gitStatuses.get(entry.path)!} />}
        {containsChange(entry.path) && <span className="desktop-change-dot" role="img" aria-label={directory ? "Contains changed files" : "Changed"} />}
      </button>{disabled && <p id={detailId} className="sr-only">{detail}</p>}
      {directory && expanded && tree(entry.path, depth + 1)}
    </li>;
  };
  const tree = (path: string, depth: number): React.ReactNode => {
    const state = model.directories[path];
    return <div className="desktop-directory">
      {state?.error && <p>{state.error}</p>}
      {state?.loading && <p>Loading folder…</p>}
      {state?.page && <><ul>{state.page.entries.map(entry => row(entry, depth))}</ul>
        {!state.page.entries.length && <p>Empty folder</p>}
        {state.page.warnings.map(warning => <p key={warning}>{warning}</p>)}
        {state.page.limited && <p>Showing a captured subset of more than 10,000 entries. Use the filter.</p>}
        {state.paged && <BtnGhost onClick={() => void model.loadDirectory(path)}>First page</BtnGhost>}
        {state.page.nextCursor && <BtnGhost disabled={state.loading} onClick={() => void model.loadDirectory(path, state.page!.nextCursor)}>Next 200 entries</BtnGhost>}
      </>}
      {(!state || state.error) && <BtnGhost onClick={() => void model.loadDirectory(path)}>Reload folder</BtnGhost>}
    </div>;
  };
  return <aside aria-label="Workspace files" className="desktop-file-navigator">
    <div role="status" aria-live="polite" className="sr-only">{loading ? "Searching workspace paths" : error || (Object.values(model.directories).some(value => value.loading) ? "Loading workspace folders" : "")}</div>
    <header className="desktop-file-header">
      <div className="desktop-project-chip" title={project}><Icon name="folder" size={16} /><span>{project}</span></div>
      <div className="desktop-file-filter"><label><Icon name="search" size={14} /><input aria-label="Filter workspace paths" value={query} maxLength={256} onChange={event => setQuery(event.target.value)} placeholder="Filter files…" /></label><button type="button" className="desktop-icon" onClick={() => { setQuery(""); void model.loadDirectory(""); }} aria-label="Refresh workspace files" title="Refresh files"><Icon name="retry" size={14} /></button></div>
      <div className="desktop-ignore-controls"><label className="desktop-ignored-option"><input type="checkbox" checked={includeIgnored} onChange={event => setIncludeIgnored(event.target.checked)} />Include ignored</label><span tabIndex={0} className="desktop-ignore-info" aria-label="Ignore information: local patterns only; tracked files and linked-worktree or global exclusions may differ from Git." title="Local patterns only. Tracked files and linked-worktree/global exclusions may differ from Git."><Icon name="info" size={14} /></span></div>
    </header>
    <div className="desktop-file-scroll">
      {git?.enabled && <GitComparisonCaption model={git} />}
      {(!!changedPaths.length || git?.enabled) && !query.trim() && <section role="region" aria-label="Changed files"><button type="button" className="desktop-file-section" aria-expanded={changesOpen} onClick={() => setChangesOpen(!changesOpen)}><Icon name={changesOpen ? "chev-d" : "chev-r"} size={12} /><span>Changed files</span><small>{changedScope === "git" && git?.enabled ? gitResult?.changes.length ?? "—" : changedPaths.length}</small></button>{changesOpen && <>
        {git?.enabled && <label className="desktop-review-scope">Scope<select aria-label="Changed files scope" value={changedScope} onChange={event => setChangedScope(event.target.value === "git" ? "git" : "conversation")}><option value="conversation">Conversation edits</option><option value="git">Uncommitted vs HEAD</option></select></label>}
        {changedScope === "git" && git?.enabled ? <GitNavigatorChanges model={git} review={reviewGit} /> : <><p className="desktop-change-evidence">Conversation edits and changed previews</p>{changedPaths.length ? changeRows(changedTree) : <p className="desktop-change-evidence">No observed conversation edits or changed previews.</p>}{changedPaths.length > 100 && <p>Showing the first 100 changed paths.</p>}</>}
      </>}</section>}
      <section aria-label="All files"><button type="button" className="desktop-file-section" aria-expanded={allOpen} onClick={() => setAllOpen(!allOpen)}><Icon name={allOpen ? "chev-d" : "chev-r"} size={12} /><span>{query.trim() ? "Matching files" : "All files"}</span></button>
      {allOpen && (query.trim() ? <>{loading && <p>Searching paths…</p>}{error && <p>{error}</p>}{search && <><ul>{search.entries.map(entry => row(entry, 0))}</ul>{!search.entries.length && <p>No matching paths.</p>}{search.warnings.map(warning => <p key={warning}>{warning}</p>)}</>}</> : tree("", 0))}</section>
    </div>
  </aside>;
}
export function WorkspaceFileBody({ model }: { model: ProjectFiles }) {
  const tab = model.tabs.find(value => value.path === model.selection), [wrap, setWrap] = useState(true), [source, setSource] = useState(false);
  useEffect(() => { setSource(false); }, [tab?.path]);
  if (!tab) return <div className="desktop-files-landing"><CardShell icon="folder" title="Files"><div className="desktop-files-intro"><p>Browse your project, including hidden files and empty folders.</p><p>Previews stay on this computer. They enter the conversation only when you attach them.</p><BtnPrimary icon="folder" onClick={() => model.setNavigator(true)}>Browse workspace files</BtnPrimary></div></CardShell>{model.changed.length > 0 && <section className="desktop-file-suggestions"><h3>Observed conversation edits</h3><ul>{model.changed.slice(0, 20).map(path => <li key={path}><button type="button" title={path} onClick={() => model.open(path)}><Icon name={fileIcon(path)} size={14}/><span>{path}</span></button></li>)}</ul></section>}{model.recent.length > 0 && <section className="desktop-file-suggestions"><h3>Recently opened</h3><ul>{model.recent.map(path => <li key={path}><button type="button" title={path} onClick={() => model.open(path)}><Icon name={fileIcon(path)} size={14}/><span>{path}</span></button></li>)}</ul></section>}</div>;
  return <div className="desktop-file-preview">
    <FileToolbar path={tab.path} wrap={wrap} source={source} setWrap={setWrap} setSource={setSource} model={model} />
    {tab.stale && <div role="status" className="desktop-changed-preview"><button type="button" onClick={() => void model.loadFile(tab.path)} aria-label="Reload changed preview"><span className="desktop-change-dot" />Changed — Reload</button></div>}
    {tab.released && <FilePreviewNotice title="Preview released">Select this tab or reload to view it again.</FilePreviewNotice>}
    {tab.preview?.kind === "image" && <LocalImage image={tab.preview} />}
    {tab.loading && <FilePreviewNotice title="Loading preview…"/>}{tab.error && <FilePreviewNotice title="Preview unavailable">{tab.error}</FilePreviewNotice>}
    {tab.preview?.kind === "binary" && <FilePreviewNotice title="No text preview">This binary or unsupported-encoding file has no text preview.</FilePreviewNotice>}
    {tab.preview?.kind === "text" && <>{tab.preview.truncated && <p>Showing the first 256 KiB of {tab.preview.size.toLocaleString()} bytes.</p>}{/\.(md|markdown)$/i.test(tab.path) && !source ? <WorkspaceDocument key={`${tab.path}:${tab.preview.revision ?? ""}`} path={tab.path} content={tab.preview.text} model={model} /> : <SourcePreview path={tab.path} text={tab.preview.text} wrap={wrap} />}</>}
  </div>;
}

function FilePreviewNotice({title,children}:{title:string;children?:React.ReactNode}){return <div className="desktop-preview-notice" role="status"><CardShell icon="file" title={title}>{children&&<p>{children}</p>}</CardShell></div>;}
