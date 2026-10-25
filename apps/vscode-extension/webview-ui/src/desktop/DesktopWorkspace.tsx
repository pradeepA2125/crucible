import { lazy, Suspense } from "react";
const TerminalTool=lazy(()=>import("./TerminalTool").then(module=>({default:module.TerminalTool})));
import { useEffect, type Dispatch, type ReactNode } from "react";
import type { InspectionPanel } from "../components/ThreadView";
import type { ProjectFiles } from "./useProjectFiles";
import { WorkspaceNavigator, WorkspaceFileBody } from "./WorkspaceFiles";
import { WorkspaceLauncher } from "./WorkspaceLauncher";
import { GitChangesTool } from "./GitChangesTool";
import { BaselineChangesTool } from "./BaselineChangesTool";
import type { BaselineReviewModel } from "./useBaselineChanges";
import type { ChatMsg } from "../types";
import type { GitReviewModel } from "./useGitChanges";
import { ChangesTool, type ChangesModel } from "./ChangesTool";
import { workspaceTabs as reduceWorkspaceTabs } from "./workspaceTabs";
import { fileIcon } from "./fileIcons";
import type { WorkspaceTabs, WorkspaceTab, WorkspaceTabAction, WorkspaceTool } from "./workspaceTabs";
import { Icon } from "../components/Icon";
export type ChangesScope = "conversation" | "git" | "baseline";
interface Props { threadId?: string; project: string; focusRequested: boolean; focusHandled(): void; requestFocus(): void; files?: ProjectFiles; changes: ChangesModel; git?: GitReviewModel; baseline?: BaselineReviewModel; messages?: ChatMsg[]; changesScope?: ChangesScope; setChangesScope?(scope: ChangesScope): void; tabs: WorkspaceTabs; dispatch: Dispatch<WorkspaceTabAction>; visible: boolean; expanded: boolean; panels: InspectionPanel[]; overview: ReactNode; onExpand(): void; onHide(): void; }
export function DesktopWorkspace({ threadId="", project, focusRequested, focusHandled, requestFocus, visible, expanded, panels, overview, onExpand, onHide, files, changes, git, baseline, messages = [], changesScope = "conversation", setChangesScope, tabs, dispatch }: Props) {
  const panelKey = JSON.stringify(panels.map(panel => ({ id: panel.id, label: panel.label })));
  useEffect(() => { dispatch({ type: "panels", panels: panels.map(panel => ({ id: panel.id, label: panel.label })) }); }, [panelKey, dispatch]);
  if (!visible) return null;
  const active = tabs.tabs.find(tab => tab.id === tabs.active), fileSelected = active?.kind === "files" || active?.kind === "file";
  const selected = active?.kind === "inspection" ? panels.find(panel => panel.id === active.inspectionId) : undefined;
  const bodyFiles = files ? { ...files, selection: active?.kind === "file" ? active.path : null } : undefined;
  const choose = (kind: WorkspaceTool) => { files?.select(kind === "files" ? null : undefined); dispatch({ type: "tool", kind }); };
  const select = (tab: WorkspaceTab) => { files?.select(tab.kind === "file" ? tab.path : tab.kind === "files" ? null : undefined); dispatch({ type: "select", id: tab.id }); if (tab.kind === "new") requestFocus(); };
  const close = (tab: WorkspaceTab) => {
    if (tab.kind === "file") files?.close(tab.path);
    if (tab.kind === "inspection") panels.find(panel => panel.id === tab.inspectionId)?.close();
    if (tab.id === tabs.active) files?.select(undefined);
    const next = reduceWorkspaceTabs(tabs, { type: "close", id: tab.id });
    if (next.tabs.find(item => item.id === next.active)?.kind === "new") requestFocus();
    dispatch({ type: "close", id: tab.id });
  };
  const label = (tab: WorkspaceTab) => tab.kind === "new" ? "New tab" : tab.kind === "files" ? "Files" : tab.kind === "agents" ? "Agents" : tab.kind === "terminal" ? "Terminal" : tab.kind === "changes" ? "Changes" : tab.kind === "inspection" ? tab.label
    : tabs.tabs.some(other => other.kind === "file" && other.path !== tab.path && other.path.split("/").at(-1) === tab.path.split("/").at(-1)) ? tab.path : tab.path.split("/").at(-1)!;
  return <aside aria-label="Workspace" className={`desktop-workspace${active?.kind === "changes" ? " is-review" : ""}${expanded ? " is-expanded" : ""}`}>
    <div className="desktop-workspace-toolbar" hidden={active?.kind === "changes"}>
      <span className="desktop-section-label desktop-workspace-chip"><Icon name="panel-right" size={13} />Workspace</span>
      {fileSelected && files?.port && <button type="button" className="desktop-icon" aria-label="Toggle file navigator" aria-expanded={files.navigator} onClick={() => files.setNavigator(!files.navigator)}><Icon name="book" size={14} /></button>}
      <button type="button" className="desktop-icon" onClick={onExpand} aria-label={expanded ? "Restore conversation" : "Expand workspace"} title={expanded ? "Restore conversation" : "Expand workspace"}><Icon name="expand" size={14} /></button>
      <button type="button" className="desktop-icon" onClick={onHide} aria-label="Hide workspace" title="Hide workspace"><Icon name="x" size={14} /></button>
    </div>
    <div role="toolbar" aria-label="Workspace tabs" className="desktop-tabs">
      {tabs.tabs.map(tab => <div key={tab.id} className="desktop-tab-group"><button type="button" title={tab.kind === "file" ? tab.path : label(tab)} aria-pressed={tabs.active === tab.id} onClick={() => select(tab)}><Icon name={tab.kind === "new" ? "plus" : tab.kind === "agents" || tab.kind === "inspection" ? "spark" : tab.kind === "terminal" ? "term" : tab.kind === "files" ? "folder" : tab.kind === "changes" ? "diff" : fileIcon(tab.path)} size={12} />{label(tab)}{tab.kind === "file" && files?.tabs.find(file => file.path === tab.path)?.stale && <span className="desktop-change-dot" role="img" aria-label="Changed" />}</button><button type="button" className="desktop-icon" onClick={() => close(tab)} aria-label={`Close ${tab.kind === "file" ? tab.path : label(tab)}${tab.kind === "new" ? "" : " tab"}`}><Icon name="x" size={10} /></button></div>)}
      <button type="button" className="desktop-icon desktop-add-tab" aria-label="New workspace tab" onClick={() => { files?.select(undefined); if (tabs.tabs.length < 20) requestFocus(); dispatch({ type: "new" }); }}><Icon name="plus" size={14} /></button>
      {active?.kind === "changes" && <><button type="button" className="desktop-icon" onClick={onExpand} aria-label={expanded ? "Restore conversation" : "Expand workspace"} title={expanded ? "Restore conversation" : "Expand workspace"}><Icon name="expand" size={14} /></button><button type="button" className="desktop-icon" aria-label="Hide workspace" title="Hide workspace" onClick={onHide}><Icon name="x" size={14} /></button></>}
    </div>
    {(tabs.notice || files?.notice) && <p role="status" className="desktop-workspace-notice">{tabs.notice || files?.notice}</p>}
    <div className="desktop-workspace-columns"><div id="workspace-content" role="region" aria-label={active?.kind === "new" ? "Workspace launcher" : fileSelected ? "File inspection" : active?.kind === "changes" ? "Change review" : active?.kind === "terminal" ? "Terminal and output" : "Agent inspection"} className="desktop-workspace-body">
      {active?.kind === "new" ? <WorkspaceLauncher focusRequested={focusRequested} focusHandled={focusHandled} key={active.id} hasFiles={!!files?.port} recent={files?.recent ?? []} changed={files?.changed ?? []} choose={choose} open={path => files?.open(path)} />
         : active?.kind === "changes" ? <div className="desktop-review">
          {(() => {
            const scopePicker = git?.enabled || baseline?.enabled ? <select aria-label="Changes scope" value={changesScope} onChange={event => {
              const scope: ChangesScope = event.target.value === "git" ? "git" : event.target.value === "baseline" ? "baseline" : "conversation"; setChangesScope?.(scope);
              if (scope === "git" && git && !git.result && !git.loading) void git.refresh();
              if (scope === "baseline") void baseline?.open();
            }}><option value="conversation">Conversation edits</option>{git?.enabled && <option value="git">Uncommitted vs HEAD</option>}{baseline?.enabled && <option value="baseline">Files edited by the agent since a checkpoint</option>}</select> : undefined;
            return changesScope === "baseline" && baseline ? <BaselineChangesTool model={baseline} messages={messages} scope={scopePicker} /> : changesScope === "git" && git ? <GitChangesTool model={git} scope={scopePicker} /> : <ChangesTool model={changes} scope={scopePicker} />;
          })()}
        </div>
        : active?.kind === "terminal" ? <Suspense fallback={<p>Opening terminal view…</p>}><TerminalTool threadId={threadId} /></Suspense> : fileSelected && bodyFiles ? <WorkspaceFileBody model={bodyFiles} /> : selected?.content ?? overview}
    </div>{fileSelected && files?.port && files.navigator && <WorkspaceNavigator project={project} model={files} git={git} reviewGit={path => { setChangesScope?.("git"); if (path) git?.select(path); choose("changes"); }} />}</div>
  </aside>;
}
