import { Projects } from "./Projects";
import { ProjectAccess } from "./ProjectAccess";
import { FindBar } from "./FindBar";
import { CommandPalette, type PaletteItem } from "./CommandPalette";
import { PinnedSummary } from "./PinnedSummary";
import { ThreadSidebar } from "./ThreadSidebar";
import type { Metadata, ThreadMetadataPort } from "./threadMetadataClient";
import { useCallback, useEffect, useMemo, useState, useRef, useReducer, type CSSProperties } from "react";
import { useAppState } from "../hooks/useAppState";
import { vscode } from "../vscodeApi";
import { FullAccessFirstSendDialog } from "../components/FullAccessFirstSendDialog";
import { ChatAdmissionStatus, ThreadView, type ThreadWorkspace } from "../components/ThreadView";
import { Icon } from "../components/Icon";
import type { ThreadSummary } from "../types";
import { ReviewDraft, type RegisterReviewDraft, type ReviewDraftSink } from "../drafts/reviewDraft";
import type { DraftPort } from "../drafts/types";
import { INITIAL_WORKSPACE_TABS, workspaceTabs } from "./workspaceTabs";
import { useProjectFiles } from "./useProjectFiles";
import type { WorkspacePort } from "./workspaceClient";
import { useBaselineChanges } from "./useBaselineChanges";
import type { BaselinePort } from "./baselineClient";
import type { ChangesScope } from "./DesktopWorkspace";
import { DesktopWorkspace } from "./DesktopWorkspace";
import { useGitChanges } from "./useGitChanges";
import type { ChangesPort } from "./changesClient";
import { conversationChanges, recordedEditTarget, type ConversationChange } from "./conversationChanges";
import type { ChangesModel, ExactEditSelection } from "./ChangesTool";
import type { RecordedEditSource } from "../transcript/groupTranscript";
import { ChangesRoute } from "../components/activity/changesRoute";
import { useDesktopLayout } from "./useDesktopLayout";
import type { LayoutPort } from "./layoutClient";
import { ResizeHandle } from "./ResizeHandle";
import { LAYOUT_BOUNDS, DEFAULT_LAYOUT } from "../../../../desktop/src/layout";
import { layoutGeometry } from "./layoutGeometry";
import "./desktop.css";

export function desktopThreadStatus(thread: ThreadSummary, active: boolean, turnActive: boolean, gates: number): ThreadSummary["status"] {
  if ((active ? gates : thread.pendingGates ?? 0) > 0) return "review";
  if (active ? turnActive : thread.turnActive === true) return "running";
  // Running requires turn liveness; other explicit legacy states remain visible.
  return thread.status === "running" ? null : thread.status;
}
export default function DesktopApp({ draftStore, layoutStore, fileStore, changesStore, baselineStore, threadMetadata }: { threadMetadata?: ThreadMetadataPort; draftStore?: DraftPort; layoutStore?: LayoutPort; fileStore?: WorkspacePort; changesStore?: ChangesPort; baselineStore?: BaselinePort }) {
  const { state, setView } = useAppState();
  const { layout, update: updateLayout, error: layoutError, retry: retryLayout } = useDesktopLayout(layoutStore);
  const sidebar = layout.sidebarVisible;
  const shell = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState(() => window.innerWidth);
  const [threadNames, setThreadNames] = useState<Metadata>({});
  const [paletteOpen, setPaletteOpen] = useState(false), [summaryOpen, setSummaryOpen] = useState(false);
  const [findOpen,setFindOpen]=useState(false);
  const closeFind=useCallback(()=>setFindOpen(false),[]);
  const [navigating, setNavigating] = useState(false), [navigationError, setNavigationError] = useState("");
  const [workspaceOpen, setWorkspaceOpen] = useState(false), [expanded, setExpanded] = useState(false);
  const files = useProjectFiles(fileStore, workspaceOpen);
  const [launcherFocus, setLauncherFocus] = useState(false);
  const requestLauncherFocus = useCallback(() => setLauncherFocus(true), []), launcherFocusHandled = useCallback(() => setLauncherFocus(false), []);
  const [workspaceTabsState, dispatchWorkspaceTab] = useReducer(workspaceTabs, INITIAL_WORKSPACE_TABS);
  const filePaths = JSON.stringify(files.tabs.map(tab => tab.path));
  useEffect(() => { dispatchWorkspaceTab({ type: "syncFiles", paths: files.tabs.map(tab => tab.path) }); }, [filePaths]);
  useEffect(() => { if (typeof files.selection === "string") dispatchWorkspaceTab({ type: "file", path: files.selection }); }, [files.selection, workspaceTabsState.tabs.length]);
  const { sidebarWidth, sidebarMax, workspaceWidth, workspaceMax, compact, isExpanded } = layoutGeometry(viewport, layout, workspaceOpen, expanded);
  const [query, setQuery] = useState(""), [project, setProject] = useState("Project");
  const [readyThread, setReadyThread] = useState("");
  const [openingDelayed, setOpeningDelayed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [now, setNow] = useState(() => new Date());
  const [dismissedErrorTaskId, setDismissedErrorTaskId] = useState<string | null>(null);
  useEffect(() => {
    const resized = () => setViewport(shell.current?.getBoundingClientRect().width || window.innerWidth);
    window.addEventListener("resize", resized);
    const tick = setInterval(() => setNow(new Date()), 60000);
    const receive = (event: MessageEvent) => {
      if (event.data?.type === "desktop/fileOpen" && typeof event.data.path === "string") { files.open(event.data.path); setWorkspaceOpen(true); }
      if (event.data?.type === "liveStatus" && typeof event.data.threadId === "string") setReadyThread(event.data.threadId);
      if (event.data?.type === "desktop/project" && typeof event.data.name === "string") setProject(event.data.name);
      if (event.data?.type === "desktop/draftError") setNavigationError(event.data.message);
    };
    window.addEventListener("message", receive);
    return () => { clearInterval(tick); window.removeEventListener("resize", resized); window.removeEventListener("message", receive); };
  }, []);
  useEffect(() => {
    setOpeningDelayed(false);
    if (state.activeThreadId && readyThread === state.activeThreadId) return;
    const timer = setTimeout(() => setOpeningDelayed(true), 8000);
    return () => clearTimeout(timer);
  }, [state.activeThreadId, readyThread, retry]);
  // Changes: derived from this conversation's applied edit records. Viewed marks are per
  // conversation and file, kept for this session, and lapse when the file gets a new edit.
  const git = useGitChanges(changesStore, fileStore ? files.open : undefined);
  const baseline = useBaselineChanges(baselineStore, fileStore ? files.open : undefined);
  const [changesScope, setChangesScope] = useState<ChangesScope>("conversation");
  const changes = useMemo(() => conversationChanges(state.messages), [state.messages]);
  const latestReview = useRef({ threadId: state.activeThreadId, messages: state.messages });
  latestReview.current = { threadId: state.activeThreadId, messages: state.messages };
  const editRequest = useRef(0);
  const [reviewComposer, setReviewComposer] = useState<ReviewDraftSink>();
  const registerReviewDraft = useCallback<RegisterReviewDraft>(sink => {
    if (sink.threadId !== latestReview.current.threadId) return () => {};
    setReviewComposer(sink);
    return () => setReviewComposer(current => current === sink ? undefined : current);
  }, []);
  const reviewSink = useMemo<ReviewDraftSink | undefined>(() => reviewComposer?.threadId === state.activeThreadId ? {
    ...reviewComposer, append: text => latestReview.current.threadId === reviewComposer.threadId ? reviewComposer.append(text) : "This conversation changed. Add the comment again in the intended conversation.",
  } : undefined, [reviewComposer, state.activeThreadId]);
  const [changesSelection, setChangesSelection] = useState<{ threadId: string | null; path?: string; target?: ExactEditSelection }>();
  const changesPath = changesSelection?.threadId === state.activeThreadId ? changesSelection?.path : undefined;
  const setChangesPath = useCallback((path?: string) => setChangesSelection({ threadId: state.activeThreadId, path }), [state.activeThreadId]);
  const changesTarget = changesSelection?.threadId === state.activeThreadId ? changesSelection.target : undefined;
  // Passive scroll-following must retain the source record and its outline.
  const highlightChangesPath = useCallback((path: string) => setChangesSelection(value => value?.threadId === state.activeThreadId
    ? value.path === path ? value : { ...value, path } : { threadId: state.activeThreadId, path }), [state.activeThreadId]);
  const [viewed, setViewedMarks] = useState<ReadonlyMap<string, string>>(() => new Map());
  const viewedKey = useCallback((change: ConversationChange) => `${state.activeThreadId ?? ""}\u0000${change.path}`, [state.activeThreadId]);
  // ChangesRoute is a context value: message appends must not change its identity.
  const showChanges = useCallback((path?: string, source?: RecordedEditSource) => {
    const { threadId, messages } = latestReview.current;
    const resolved = source && path !== undefined ? recordedEditTarget(messages, source, path) : undefined;
    const target = source && path !== undefined ? { ...(resolved ?? { path, key: "", signature: "", editsInTurn: 0, lastInTurn: false }), request: ++editRequest.current } : undefined;
    setChangesScope("conversation"); setChangesSelection({ threadId, path, target }); files.select(undefined);
    dispatchWorkspaceTab({ type: "tool", kind: "changes" }); setWorkspaceOpen(true);
  }, [files.select]);
  const changesModel = useMemo<ChangesModel>(() => ({
    changes, selected: changesPath, select: setChangesPath, target: changesTarget, highlight: highlightChangesPath,
    isViewed: change => viewed.get(viewedKey(change)) === change.signature,
    setViewed: (change, mark) => setViewedMarks(value => {
      const next = new Map(value); next.delete(viewedKey(change));
      if (mark) { next.set(viewedKey(change), change.signature); while (next.size > 1000) next.delete(next.keys().next().value!); }
      return next;
    }),
    ...(fileStore ? { openFile: (path: string) => files.open(path) } : {}),
  }), [changes, changesPath, setChangesPath, changesTarget, highlightChangesPath, viewed, viewedKey, fileStore, files.open]);
  const openWorkspace = useCallback(() => { files.select(undefined); dispatchWorkspaceTab({ type: "tool", kind: "agents" }); setWorkspaceOpen(true); }, [files.select]);
  const hideWorkspace = useCallback(() => setWorkspaceOpen(false), []);
  const toggleExpanded = useCallback(() => { if (compact) setWorkspaceOpen(false); else setExpanded(value => !value); }, [compact]);
  const workspace = useMemo<ThreadWorkspace>(() => ({ visible: workspaceOpen, expanded: isExpanded, open: openWorkspace,
    render: (panels, overview) => <>
      {workspaceOpen && !isExpanded && <ResizeHandle label="Resize workspace" value={workspaceWidth} min={LAYOUT_BOUNDS.workspaceMin} max={workspaceMax} defaultValue={DEFAULT_LAYOUT.workspaceWidth} direction={-1}
        preview={value => shell.current?.style.setProperty("--desktop-workspace-width", `${value}px`)} commit={value => updateLayout({ workspaceWidth: value })} />}
      <DesktopWorkspace threadId={state.activeThreadId??""} project={project} focusRequested={launcherFocus} focusHandled={launcherFocusHandled} requestFocus={requestLauncherFocus} tabs={workspaceTabsState} dispatch={dispatchWorkspaceTab} files={files} changes={changesModel} git={git} baseline={baseline} messages={state.messages} changesScope={changesScope} setChangesScope={setChangesScope} visible={workspaceOpen} expanded={isExpanded} panels={panels} overview={overview} onExpand={toggleExpanded} onHide={hideWorkspace} />
    </>,
  }), [workspaceOpen, isExpanded, workspaceWidth, workspaceMax, openWorkspace, toggleExpanded, hideWorkspace, files, changesModel, git, baseline, state.messages, state.activeThreadId, changesScope, workspaceTabsState, project, launcherFocus, launcherFocusHandled, requestLauncherFocus]);
  const locked = navigating || !!state.chatAdmission || (!state.historyState?.navigationSafe && readyThread === state.activeThreadId && !!state.activeThreadId && (!state.inputEnabled || state.turnActive));
  const selected = state.threads.find(thread => thread.threadId === state.activeThreadId);
  const navigate = async (message: { type: "newChat" } | { type: "switchThread"; threadId: string }) => {
    if (locked) return;
    setNavigating(true); setNavigationError("");
    try { await draftStore?.flush?.(); vscode.postMessage(message); setView("thread"); }
    catch (error) { setNavigationError(error instanceof Error ? error.message : String(error)); }
    finally { setNavigating(false); }
  };
  const newConversation = () => { void navigate({ type: "newChat" }); };
  const select = (threadId: string) => { if (threadId !== state.activeThreadId) void navigate({ type: "switchThread", threadId }); };
  const sidebarThreads = state.threads.map(thread => thread.threadId === state.activeThreadId ? { ...thread,
    pendingGates: state.historyState?.phase === "current" ? state.liveGates.length : thread.pendingGates,
    gateKinds: state.historyState?.phase === "current" ? state.liveGates.map(g => g.kind) : thread.gateKinds,
    status: desktopThreadStatus(thread, true, state.turnActive, state.liveGates.length) } : thread);
  const openFile = (path: string) => { files.open(path); setWorkspaceOpen(true); };
  useEffect(()=>{
    const run=(action:string)=>{
      if(document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      if(action==="new") newConversation();
      else if(action==="open") vscode.postMessage({type:"desktop/openProject"} as never);
      else if(action==="settings") vscode.postMessage({type:"openSettings"});
      else if(action==="palette") setPaletteOpen(true);
      else if(action==="find") {setFindOpen(true);document.querySelector<HTMLInputElement>('[aria-label="Find text"]')?.focus();}
      else if(action==="sidebar") updateLayout({sidebarVisible:!sidebar});
      else if(action==="workspace") setWorkspaceOpen(value=>!value);
    };
    const receive=(event:MessageEvent)=>{if(event.data?.type==="desktop/shortcut")run(event.data.action);};
    // Native accelerators normally own these keys. Fallback preserves browser/test operation.
    const key=(event:KeyboardEvent)=>{if(event.defaultPrevented || !(event.metaKey||event.ctrlKey) || event.altKey || event.shiftKey || (navigator.platform.includes("Mac") && event.ctrlKey))return;const action=event.key.toLowerCase()==="k" ? "palette" : event.key.toLowerCase()==="f" ? "find" : undefined;if(action){event.preventDefault();run(action);}};
    window.addEventListener("message",receive);window.addEventListener("keydown",key);
    return()=>{window.removeEventListener("message",receive);window.removeEventListener("keydown",key);};
  },[locked,navigating,draftStore,state.activeThreadId,sidebar,updateLayout]);
  const paletteItems: PaletteItem[] = [
    ...state.threads.slice(0,1000).map(thread => ({ id: `thread:${thread.threadId}`, label: threadNames[thread.threadId]?.name ?? thread.title, detail: "Conversation", disabled: locked, run: () => select(thread.threadId) })),
    { id: "new", label: "New conversation", detail: "Action", disabled: locked, run: newConversation },
    { id:"open",label:"Open project…",detail:"Action · Open or switch projects",run:()=>vscode.postMessage({type:"desktop/openProject"} as never) },
    { id:"runtime",label:"Runtime status",detail:"Action",run:()=>vscode.postMessage({type:"openSettings",section:"runtime"}) },
    { id:"find",label:"Find in visible view",detail:"Action",run:()=>setFindOpen(true) },
    {id:"terminal",label:"Open Terminal and agent output",detail:"Action",run:()=>{dispatchWorkspaceTab({type:"tool",kind:"terminal"});setWorkspaceOpen(true);}},
    { id: "files", label: "Browse workspace files", detail: "Action", run: () => { dispatchWorkspaceTab({type:"tool",kind:"files"}); setWorkspaceOpen(true); } },
    { id: "changes", label: "Review conversation changes", detail: "Action", run: () => { setChangesScope("conversation"); dispatchWorkspaceTab({type:"tool",kind:"changes"}); setWorkspaceOpen(true); } },
    ...[{id:"memory",label:"Memory Inspector",type:"openMemoryPanel"},{id:"axon",label:"AXON · Dependency Space",type:"openGraphPanel"},{id:"settings",label:"Settings",type:"openSettings"},{id:"setup",label:"Run Setup",type:"desktop/openSetup"}].map(item => ({ id:item.id,label:item.label,detail:"Action",run:()=>vscode.postMessage({type:item.type} as never) })),
    { id:"summary",label:summaryOpen ? "Hide pinned summary" : "Show pinned summary",detail:"Action",run:()=>setSummaryOpen(value=>!value) },
  ];
  return <div ref={shell} className="desktop-shell" style={{ "--desktop-sidebar-width": `${sidebarWidth}px`, "--desktop-workspace-width": `${workspaceWidth}px`, "--desktop-chat-reserve": `${LAYOUT_BOUNDS.chatMin + LAYOUT_BOUNDS.divider}px` } as CSSProperties}>
    <FullAccessFirstSendDialog />
    {paletteOpen && <CommandPalette items={paletteItems} files={fileStore} openFile={openFile} close={() => setPaletteOpen(false)} /> }
    {sidebar && <aside className="desktop-sidebar" aria-label="Conversations">
      <div className="desktop-brand"><span className="desktop-mark"><Icon name="spark" size={15} /></span><strong>Crucible</strong><span className="desktop-label">Desktop</span></div>
      <button type="button" className="desktop-new" disabled={locked} onClick={newConversation}><Icon name="plus" size={15} />New conversation</button>
      <button type="button" className="desktop-palette-trigger" onClick={() => setPaletteOpen(true)}><Icon name="search" size={13}/><span>Search everything</span><kbd>⌘/Ctrl K</kbd></button>
      <label className="desktop-search"><Icon name="search" size={13} /><input aria-label="Search conversations" placeholder="Search conversations" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <div className="desktop-projects"><Projects locked={navigating || !!state.chatAdmission} /><ThreadSidebar threads={sidebarThreads} active={state.activeThreadId} project={project} query={query}
        locked={locked} now={now} groupOpen={layout.groupExpanded} onGroupToggle={() => updateLayout({ groupExpanded: !layout.groupExpanded })} port={threadMetadata} onSelect={select} onMetadata={setThreadNames} ready={state.historyState?.phase === "current"} /></div>
      <div className="desktop-sidebar-views"><button type="button" onClick={()=>vscode.postMessage({type:"desktop/openProject"} as never)}><Icon name="folder" size={14}/><span>Open project…</span></button><button type="button" onClick={() => vscode.postMessage({type:"openMemoryPanel"})}><Icon name="db" size={14}/><span>Memory Inspector</span></button><button type="button" onClick={() => vscode.postMessage({type:"openGraphPanel"})}><Icon name="orbit" size={14}/><span>AXON<span className="desktop-view-description">Dependency Space</span></span></button></div>
      <div className="desktop-sidebar-footer"><button type="button" onClick={() => vscode.postMessage({ type: "openSettings" })}><Icon name="gear" size={15} />Settings</button><ProjectAccess threadId={state.activeThreadId ?? ""} title={selected?.title ?? ""} /></div>
    </aside>}
    {sidebar && <ResizeHandle label="Resize conversations" value={sidebarWidth} min={LAYOUT_BOUNDS.sidebarMin} max={Math.max(LAYOUT_BOUNDS.sidebarMin, sidebarMax)} defaultValue={DEFAULT_LAYOUT.sidebarWidth}
      preview={value => shell.current?.style.setProperty("--desktop-sidebar-width", `${value}px`)} commit={value => updateLayout({ sidebarWidth: value })} />}
    <main className="desktop-main">
      <header className="desktop-header">
        <button type="button" className="desktop-icon" aria-label={sidebar ? "Hide conversations" : "Show conversations"} aria-expanded={sidebar} onClick={() => updateLayout({ sidebarVisible: !sidebar })} title="Toggle conversations"><Icon name="menu" size={16} /></button>
        <span className="desktop-header-project">{project}</span><span className="desktop-header-divider">/</span>
        <h1>{threadNames[state.activeThreadId]?.name ?? selected?.title ?? "New conversation"}</h1>
        {isExpanded && state.liveGates.length > 0 && <button type="button" className="desktop-approval" onClick={() => { setExpanded(false); setWorkspaceOpen(false); }}>Approval needed · Show conversation</button>}
        {state.agentsRunning > 0 && <button type="button" className="desktop-stop" onClick={() => vscode.postMessage({ type: "stopAllAgents" })}><Icon name="stop" size={12} />Stop all agents</button>}
        <button type="button" className="desktop-icon" aria-label={summaryOpen ? "Hide pinned summary" : "Show pinned summary"} aria-expanded={summaryOpen} onClick={() => setSummaryOpen(value => !value)}><Icon name="list" size={16} /></button>
        <button type="button" className="desktop-icon" aria-label="Search conversations, files and actions" onClick={() => setPaletteOpen(true)}><Icon name="search" size={16} /></button>
        <button type="button" className={`desktop-icon${workspaceOpen ? " is-active" : ""}`} aria-label={workspaceOpen ? "Close workspace" : "Open workspace"} aria-expanded={workspaceOpen} onClick={() => { if (!workspaceOpen && workspaceTabsState.tabs.find(tab => tab.id === workspaceTabsState.active)?.kind === "new") requestLauncherFocus(); setWorkspaceOpen(value => !value); }} title="Toggle workspace"><Icon name="panel-right" size={17} /></button>
      </header>
      <FindBar open={findOpen} close={closeFind} target={`${state.activeThreadId}:${workspaceOpen}:${isExpanded}:${workspaceTabsState.active}:${changesScope}`} />
      {summaryOpen && <PinnedSummary state={state} changes={changes} review={() => { setChangesScope("conversation"); dispatchWorkspaceTab({type:"tool",kind:"changes"}); setWorkspaceOpen(true); }} />}
      {navigationError && <div role="alert" className="desktop-layout-error">{navigationError}</div>}
      {layoutError && <div role="status" className="desktop-layout-error">{layoutError} <button type="button" onClick={retryLayout}>Retry saving layout</button></div>}
      <section className="desktop-conversation" aria-label="Conversation">
        {state.activeThreadId && (readyThread === state.activeThreadId || state.historyState?.threadId === state.activeThreadId) ? <ChangesRoute.Provider value={showChanges}><ReviewDraft.Provider value={reviewSink}><ThreadView key={state.activeThreadId} state={state} chrome="embedded" workspace={workspace} draftStore={draftStore} registerReviewDraft={registerReviewDraft}
          onBack={() => {}} dismissedErrorTaskId={dismissedErrorTaskId} onDismissError={setDismissedErrorTaskId} /></ReviewDraft.Provider></ChangesRoute.Provider>
          : <div className="desktop-opening" role={state.chatAdmission ? undefined : "status"}><div>
            <ChatAdmissionStatus admission={state.chatAdmission} />
            {openingDelayed ? <div><p>{state.chatAdmission ? "Conversation status is unavailable. Check receipt before continuing." : "Conversation status is unavailable. Retry or choose another conversation."}</p><button type="button" className="desktop-new" disabled={!!state.chatAdmission} onClick={() => { setRetry(value => value + 1); vscode.postMessage(state.activeThreadId ? { type: "switchThread", threadId: state.activeThreadId } : { type: "newChat" }); }}>Retry</button></div> : "Opening your conversation…"}
          </div></div>}
      </section>
    </main>
  </div>;
}
