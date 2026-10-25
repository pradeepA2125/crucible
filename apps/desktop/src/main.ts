import { DesktopUpdates } from "./updates.js";
import { updateRequest } from "./update-protocol.js";
import { provisionEnvironment, DesktopProvisioner } from "./runtime-provision.js";
import { rememberWindow, restoredWindow } from "./window-state.js";
import { localDiagnostics } from "./local-diagnostics.js";
import { restoreRequest, restoreReply } from "./restore-protocol.js";
import { LifecycleWindow } from "./lifecycle-window.js";
import { LifecycleTransitions, cleanAll, quitFingerprint, type Observation } from "./lifecycle.js";
import { lifecyclePreferences, type QuitPreview } from "./lifecycle-protocol.js";
import { Terminals, shellEnvironment } from "./terminals.js";
import { terminalRequest, terminalResult } from "./terminal-protocol.js";
import { ProjectRegistry, projectId, type ProjectEntry } from "./project-registry.js";
import { ProjectPool } from "./project-pool.js";
import { DraftVault } from "./draft-vault.js";
import { importLegacyCredentials } from "./project-credentials.js";
import { app, BrowserWindow, dialog, ipcMain, Menu, nativeTheme, protocol, safeStorage, session, shell, powerMonitor, clipboard, utilityProcess, Notification, powerSaveBlocker, screen } from "electron";
import { appendFileSync, mkdirSync, existsSync } from "node:fs";
import { realpath, stat } from "node:fs/promises";
import { join, dirname, basename, isAbsolute, relative } from "node:path";
import { randomUUID } from "node:crypto";
import { homedir } from "node:os";
import { APP_ORIGIN, CHANNEL, parseEnvelope, type BridgeContext } from "./contract.js";
import { assertSender, externalURL, containedPath } from "./security.js";
import { guardContents, secureSession, WINDOW_SECURITY } from "./native-security.js";
import { NativeFind } from "./native-find.js";
import { DESKTOP_SHORTCUTS, desktopShortcutForKey, type DesktopShortcut } from "./desktop-shortcuts.js";
import { Inspectors } from "./inspectors.js";
import { inspectorMemory, inspectorGraph } from "./inspector-source.js";
import { createWorkspaceFiles } from "./workspace-files.js";
import { createWorkspaceGit } from "./workspace-git.js";
import { createWorkspaceGitRequests } from "./workspace-git-requests.js";
import { createWorkspaceBaseline } from "./workspace-baseline.js";
import { createWorkspaceBaselineRequests } from "./workspace-baseline-requests.js";
import { syncBaselineThread } from "./baseline-host.js";
import type { BaselineRequest } from "./baseline-protocol.js";
import type { GitRequest } from "./git-protocol.js";
import { createWorkspaceBrowser } from "./workspace-browser.js";
import { createWorkspaceFileRequests, type WorkspaceFileRequest } from "./workspace-file-requests.js";
import { validateEditor, NativeEditorError } from "./native-editor.js";
import { createNativeFileActions, launchEditor, type NativeFileAction } from "./native-file-actions.js";
import { installTextMenu } from "./native-text-menu.js";
import { JsonState } from "./store.js";
import { LayoutPreferences } from "./layout-store.js";
import type { DesktopLayout } from "./layout.js";
import { DraftFlusher, projectReplyAllowed, quitFlushCommand } from "./draft-flush.js";
import { createProjectHost } from "./host.js";
import { normalizeThemeSettings } from "../../vscode-extension/src/theme-settings.js";
import { assertProjectAvailable, desktopRuntimeStarting } from "./runtime.js";
import { createNodeProcessDeps } from "../../vscode-extension/src/runtime/node-runtime-deps.js";
protocol.registerSchemesAsPrivileged([{ scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.setName("Crucible");
if (process.env.CRUCIBLE_DESKTOP_PROFILE && isAbsolute(process.env.CRUCIBLE_DESKTOP_PROFILE)) app.setPath("userData", process.env.CRUCIBLE_DESKTOP_PROFILE);
let window: BrowserWindow | undefined;
let inspectors: Inspectors | undefined;
let lifecycleWindow: LifecycleWindow | undefined;
let finder: NativeFind | undefined;
let pendingShortcut: DesktopShortcut | undefined;
let pendingFile: string | undefined;
let host: ReturnType<typeof createProjectHost> | undefined;
const terminals=new Terminals(()=>utilityProcess.fork(join(__dirname,"terminal-worker.cjs"),[],{env:shellEnvironment(process.env),execArgv:[],stdio:"ignore",serviceName:"Crucible Your shell"}));
let terminalLaunchPending=false;
let meta: JsonState;
let updates:DesktopUpdates|undefined;
let pendingUpdate:Awaited<ReturnType<DesktopUpdates["prepare"]>>|undefined;
let installAfterCleanup:(()=>Promise<void>)|undefined;
let windowMemory: ReturnType<typeof rememberWindow>|undefined;
let diagnose: ReturnType<typeof localDiagnostics> = ()=>{};
const provisioner=new DesktopProvisioner(()=>{
 if(closing || closed)throw new Error("Crucible is closing.");
 if(desktopRuntimeStarting())throw new Error("A project backend is starting. Wait before installing runtime packages.");
 for(const {id} of pool.summaries()){const p=pool.get(id);if(p?.runtime.backendUrl(registry.get(id).root))throw new Error("A project backend is connected. Close its application session before changing runtime packages.");}
},provisionEnvironment(process.env),join(__dirname,"resources"));
let registry: ProjectRegistry;
const pool = new ProjectPool<ReturnType<typeof createProjectHost>>();
const slots = new Map<string,{viewId:string;processId:string|null;document:string;threadTarget?:string}>();
let projectsTimer: ReturnType<typeof setInterval> | undefined;
const persistence=()=>meta.get<boolean>("desktop.encryptedDrafts",false)===true;
const draftDirectory=()=>join(app.getPath("userData"),"drafts");
const publishProjects=async()=>{
  const view=context.viewId, projects=await registry.status();
  if(view!==context.viewId)return;
  const statuses=pool.summaries();
  post({type:"desktop/projects",selected:pool.selected??null,projects:projects.map(p=>({...p,...statuses.find(s=>s.id===p.id)}))});
};
const persistenceStatus=()=>post({type:"desktop/persistence",enabled:persistence(),available:DraftVault.available(safeStorage),
  project:context.projectId?registry.get(context.projectId):null, recovery:host?.hasRecovery()??false, legacy:context.projectId===meta.get("desktop.legacyCredentialCandidate") && !meta.get("desktop.legacyCredentialImport") && existsSync(join(app.getPath("userData"),"secrets.json"))});

let baselineRequests: ReturnType<typeof createWorkspaceBaselineRequests> | undefined;
let gitRequests: ReturnType<typeof createWorkspaceGitRequests> | undefined;
let fileRequests: ReturnType<typeof createWorkspaceFileRequests> | undefined;
let fileActions: ReturnType<typeof createNativeFileActions> | undefined;
let settingsSection: string | undefined;
let opening = false;
let projectOpenGeneration = 0;
let openingLease: object | undefined;
let closing = false;
let closed = false;
let sequence = 0;
let draftReady = false;
let rendererAlive = true;
let systemShutdown = false;
const draftFlusher = new DraftFlusher();
let context: BridgeContext = { v: 1, clientId: randomUUID(), viewId: randomUUID(), projectId: null, threadId: null, processId: null };
const post = (payload: Record<string, unknown>) => {
  syncBaselineThread(context, payload, () => baselineRequests?.cancel());
  if (window && !window.isDestroyed()) window.webContents.send(CHANNEL.event, { context:{...context}, payload: payload.type === "desktop/project" ? { ...payload, projectId: context.projectId } : payload });
};
const openExternal = async (raw: string) => { const url = externalURL(raw); if (!url) throw new Error("Only secure web links can be opened."); await shell.openExternal(url); };
app.on("web-contents-created", (_event, contents) => guardContents(contents, openExternal));
const theme = () => normalizeThemeSettings({ appearance: meta.get("crucible.appearance"), accent: meta.get("crucible.accent") });
const themeMessage = () => { const value = theme(); return { type: "themeChanged", ...value, host: "desktop", configuredAppearance: value.appearance, appearance: value.appearance === "vscode" ? nativeTheme.shouldUseDarkColors ? "dark" : "light" : value.appearance }; };
const postTheme = () => { post(themeMessage()); inspectors?.broadcastTheme(); lifecycleWindow?.broadcastTheme(); };
function restoreMainWindow():void {if(process.platform==="darwin"&&app.isReady())app.show();window?.show();window?.focus();}
const transitions = new LifecycleTransitions();
let awakeBlocker: number | undefined, suspended = false;
let cleanupStarted=false, discardApproved=false;
let delivery = "Notifications are off.", finishing = false, previewFingerprint = "", previewAt = 0;
const notices = new Map<string, Notification>();
const lifecyclePrefs = () => lifecyclePreferences.catch({notifications:"off",keepAwake:false}).parse(meta.get("desktop.lifecycle"));
const lifecycleStatus = () => post({type:"desktop/lifecycleStatus",preferences:lifecyclePrefs(),supported:Notification.isSupported(),awake:awakeBlocker!==undefined,delivery});
function releaseAwake():void { if(awakeBlocker!==undefined){powerSaveBlocker.stop(awakeBlocker);awakeBlocker=undefined;} }
function observations(values=pool.observations()):Observation[]{
  return values.map(value=>({id:value.id,name:registry.get(value.id).name,state:value.activity.state,
    owner:value.activity.evidence?.owner??false,identity:value.activity.evidence?.identity,threads:value.activity.evidence?.threads??[],
    shells:terminals.list(value.id).map((t,index)=>({...t,ordinal:index+1})).filter(t=>t.hostAlive).map(t=>({id:t.id,label:`Shell ${t.ordinal} · ${t.shell.split(/[\\/]/).pop()}`}))}));
}
function showNotification(project:Observation,threadId:string,body:string,test=false):void {
  if(!Notification.isSupported()){delivery="System notifications are unavailable.";lifecycleStatus();return;}
  const prior=notices.get(project.id);prior?.close();
  let notification:Notification;
  try{notification=new Notification({title:test?"Crucible notification test":project.name,body,silent:true});}
  catch(error){delivery=`System notification failed. ${String(error).slice(0,250)}`;lifecycleStatus();return;}
  if(!notices.has(project.id)&&notices.size>=8){const oldest=notices.keys().next().value as string;notices.get(oldest)?.close();notices.delete(oldest);}
  notices.set(project.id,notification);
  const current=()=>notices.get(project.id)===notification;
  notification.on("show",()=>{if(current()){delivery="Sent to system. Display depends on your notification settings.";lifecycleStatus();}});
  notification.on("failed",(_event,error)=>{if(current()){delivery=`System notification failed. ${String(error).slice(0,250)} Signed app builds may be required.`;lifecycleStatus();}});
  notification.on("close",()=>{if(current())notices.delete(project.id);});
  notification.on("click",()=>{void(async()=>{
    restoreMainWindow();
    if(test||closing)return;
    const fresh=await pool.get(project.id)?.activity();
    if(!fresh?.evidence || fresh.evidence.identity!==project.identity || !fresh.evidence.threads.some(t=>t.thread_id===threadId)){
      await notice("This notification is no longer current. Open the project’s conversation list to inspect its latest activity.");return;
    }
    await selectRecent(project.id,threadId);
  })().catch(error=>notice(error instanceof Error?error.message:"Conversation could not be opened."));});
  delivery=test?"Sending test to the system…":"Sending update to the system…";
  try{notification.show();}catch(error){delivery=`System notification failed. ${String(error).slice(0,250)}`;}
  lifecycleStatus();
}
function updateLifecycle(values=pool.observations()):void {
  if(!meta||!registry||finishing)return;
  const projects=observations(values),prefs=lifecyclePrefs();
  const status=transitions.update(projects,prefs,BrowserWindow.getAllWindows().some(w=>w.isFocused()));
  app.dock?.setBadge(status.badge?String(status.badge):"");
  if(status.awake&&!suspended){if(awakeBlocker===undefined)awakeBlocker=powerSaveBlocker.start("prevent-app-suspension");}else releaseAwake();
  for(const message of status.notices){const project=projects.find(p=>p.id===message.projectId)!;
    showNotification(project,message.threadId,message.kind==="attention"?`${message.count} approval request${message.count===1?"":"s"} need your attention.`:`Work completed in ${message.count} conversation${message.count===1?"":"s"}.`);}
  lifecycleStatus();
}
async function previewQuit(error?:string,phase:QuitPreview["phase"]="preview"):Promise<void>{
  if(provisioner.busy && !error)error="Runtime installation is in progress. Quitting cancels installation; reopen Setup to retry.";
  const projects=observations(await pool.refresh());
  if(systemShutdown||closed)return;
  previewFingerprint=quitFingerprint(projects);previewAt=Date.now();
  if(!lifecycleWindow)throw new Error("Quit preview is unavailable.");
  await lifecycleWindow.show({id:randomUUID(),projects,phase,...(error?{error}:{})});
}
async function finishQuit(discard=false):Promise<void>{
  if(finishing)return;finishing=true;closing=true;
  if(!cleanupStarted){
    if(!discard){
      try{
        if(draftReady){
          if(!rendererAlive)throw new Error("The conversation window is unresponsive. Retry when it reconnects, or explicitly discard its latest unsaved edits.");
          await draftFlusher.beforeQuit(context.viewId,post,true);
        }
        // All retained encrypted drafts must be durable before any teardown.
        pool.flush();
      }catch(error){
        if(!systemShutdown){finishing=false;await previewQuit(error instanceof Error?error.message:"Draft could not be saved.","draft-error");return;}
        console.error("OS shutdown could not save latest drafts; existing saved copies are kept.",error);
      }
    }
    discardApproved=discard;cleanupStarted=true;
  }
  releaseAwake();clearInterval(projectsTimer);
  try{if(systemShutdown){await pendingUpdate?.cancel();pendingUpdate=undefined;installAfterCleanup=undefined;}
    await windowMemory?.flush();await cleanAll([()=>provisioner.dispose(),()=>updates?.dispose(),()=>disposeReaders(),()=>terminals.dispose(),()=>pool.dispose(discardApproved||systemShutdown)]);
    if(installAfterCleanup&&!systemShutdown)await installAfterCleanup();
    for(const notification of notices.values())notification.close();notices.clear();lifecycleWindow?.close();closed=true;app.quit();}
  catch(error){finishing=false;
    if(systemShutdown){console.error("Shutdown cleanup incomplete:",error);closed=true;app.quit();return;}
    await previewQuit(error instanceof Error?error.message:"Some resources could not close.","cleanup-error");}
}
async function quitChoice(choice:string,preview:QuitPreview):Promise<void>{
  if(systemShutdown||finishing)return;
  if(preview.phase==="cleanup-error" && choice!=="quit")throw new Error("Cleanup has already begun. Retry quitting to stop the remaining resources.");
  if(choice==="cancel"||choice==="background"){
    await pendingUpdate?.cancel();pendingUpdate=undefined;installAfterCleanup=undefined;
    lifecycleWindow?.close();closing=false;
    if(choice==="background"){if(process.platform==="darwin")app.hide();else window?.hide();}else restoreMainWindow();return;
  }
  if(choice==="discard" && preview.phase!=="draft-error")throw new Error("No failed draft save is waiting for this choice.");
  const fresh=observations(await pool.refresh());
  if(Date.now()-previewAt>60000 || quitFingerprint(fresh)!==previewFingerprint){await previewQuit("Project activity changed. Review the updated work before quitting.",preview.phase);return;}
  await finishQuit(choice==="discard");
}
const flushDraft = async () => { if (draftReady) await draftFlusher.request(context.viewId, post); host?.flush(); };
const load = async (document: string) => {
  if(window && !window.isDestroyed()){
    const view=context.viewId,source=host;await flushDraft();
    if(view!==context.viewId || source!==host)throw new Error("Project view changed. Retry navigation from the selected project.");
    await window.loadURL(`${APP_ORIGIN}/${document}.html`);
  }
};
const navigate = (document: string) => { if(closing)return; window?.show(); window?.focus(); void load(document).catch(error => notice(error instanceof Error ? error.message : "Navigation was cancelled.")); };
const notice = async (message: string) => { if (window) await dialog.showMessageBox(window, { type: "info", title: "Crucible", message }); };
function disposeReaders(): void {
  baselineRequests?.dispose();baselineRequests=undefined;gitRequests?.dispose();gitRequests=undefined;
  fileRequests?.dispose();fileRequests=undefined;fileActions=undefined;inspectors?.dispose();finder?.stop();pendingFile=undefined;pendingShortcut=undefined;
}
async function attachProject(entry: ProjectEntry, selectedHost: ReturnType<typeof createProjectHost>, generation=projectOpenGeneration): Promise<void> {
  const current=()=>!closing&&!closed&&generation===projectOpenGeneration&&pool.get(entry.id)===selectedHost;
  if(!current())return;
  disposeReaders();host=selectedHost;
  const root=entry.root, slot=slots.get(entry.id)!;
  context={...context,viewId:randomUUID(),projectId:entry.id,threadId:null,processId:slot.processId};
  slot.viewId=context.viewId;sequence=0;draftReady=false;
    const workspaceFiles = await createWorkspaceFiles(root);
    if(!current())return;
    baselineRequests = createWorkspaceBaselineRequests(createWorkspaceBaseline(workspaceFiles, host.baselineBackend), () => ({viewId:context.viewId,projectId:context.projectId,threadId:context.threadId}), post);
    gitRequests = createWorkspaceGitRequests(createWorkspaceGit(workspaceFiles), () => ({ viewId: context.viewId, projectId: context.projectId }), post);
    fileRequests = createWorkspaceFileRequests(createWorkspaceBrowser(workspaceFiles), () => ({ viewId: context.viewId, projectId: context.projectId }), post);
    fileActions = createNativeFileActions({ platform: process.platform,
      validate: async path => { const entry = (await workspaceFiles.metadata([path])).entries[0]; if (!entry?.revision) throw new Error("This file is unavailable. Refresh Files."); return containedPath(root, path); },
      editor: () => meta.get<string>("desktop.editor"), validateEditor: editor => validateEditor(root, editor, process.platform), saveEditor: editor => meta.update("desktop.editor", editor),
      chooseEditor: async () => {
        const chosen = (await dialog.showOpenDialog(window!, { title: "Choose an editor application or executable", properties: ["openFile"] })).filePaths[0];
        if (!chosen) return undefined;
        return chosen;
      },
      launch: launchEditor,
      reveal: path => shell.showItemInFolder(path), copy: text => clipboard.writeText(text),
    });

  await meta.update("lastProject",root);
  if(!current())return;
  window!.setTitle(`Crucible — ${entry.name}`);
  // Rotate context before replacing the document. A real load emits another view epoch.
  try{await selectedHost.start();}catch(error){if(!current())return;slot.document="setup";await window!.loadURL(`${APP_ORIGIN}/setup.html`);throw error;}
  if(!current())return;
  await window!.loadURL(`${APP_ORIGIN}/${slot.document}.html`);
}
async function openProject(path?: string,threadId?:string): Promise<void> {
  if(closing)throw new Error("Finish the quit preview before opening a project.");
  if (!window || opening || closing) return;
  if(provisioner.busy){await notice("Wait for runtime installation to finish before opening a project.");return;}
  const generation=projectOpenGeneration,lease={};openingLease=lease;opening=true;
  const current=()=>!closing&&!closed&&generation===projectOpenGeneration&&openingLease===lease;
  try {
    const selected=path??(await dialog.showOpenDialog(window,{title:"Open a Crucible project",properties:["openDirectory"]})).filePaths[0];
    if(!selected||!current())return;
    const root=await realpath(selected);if(!current())return;
    if(!(await stat(root)).isDirectory())throw new Error("Choose a project folder.");
    if(!current())return;
    const id=projectId(root);pool.canAdd(id);
    const profile=app.getPath("userData");mkdirSync(profile,{recursive:true,mode:0o700});
    const log=(text:string)=>appendFileSync(join(profile,"runtime.log"),text,{mode:0o600});
    // Admit bounded recents before constructing a retained host. Refusal leaves
    // no hidden slot or process behind, including repeated registry-full opens.
    const entry=await registry.add(root,current);if(!current())return;
    let selectedHost=pool.get(id);
    if(!selectedHost){
      await assertProjectAvailable(root,createNodeProcessDeps(join(homedir(),".crucible/runtime"),{append:log,appendLine:log,show(){}}));
      if(!current())return;
      const slot:{viewId:string;processId:string|null;document:string;threadTarget?:string}={viewId:randomUUID(),processId:null,document:"desktop-app"};slots.set(id,slot);
      const selectedCurrent=()=>pool.isSelected(id) && host===selectedHost && !closing;
      try {selectedHost=createProjectHost({root,profile,draftPersistence:persistence,localTerminalBusy:()=>terminals.busy(id),
        takeThreadTarget:()=>{if(!selectedCurrent())return undefined;const target=slot.threadTarget;delete slot.threadTarget;return target;},
        ...(!app.isPackaged && process.env.CRUCIBLE_DEV_RUNTIME && isAbsolute(process.env.CRUCIBLE_DEV_RUNTIME)?{runtimeDir:process.env.CRUCIBLE_DEV_RUNTIME}:{}),
        ...(app.isPackaged?{runtimeDir:join(app.getPath("userData"),"runtime"),provisionRuntime:(deps,progress)=>provisioner.install(deps,progress)}:{}),
        canStartRuntime:()=>{if(provisioner.busy || closing)throw new Error("Wait for runtime installation or shutdown to finish before starting a backend.");},
        resources:__dirname,encryption:safeStorage,
        post:message=>{if(projectReplyAllowed(pool.isSelected(id) && host===selectedHost,closing,message.type))post(message);},viewIdentity:()=>slot.viewId,
        isOpen:()=>selectedCurrent() && !!window && !window.isDestroyed(),isFocused:()=>selectedCurrent() && (window?.isFocused()??false),
        showChat:()=>{slot.document="desktop-app";if(selectedCurrent() && !opening && new URL(window!.webContents.getURL()).pathname!=="/desktop-app.html")navigate("desktop-app");},
        showSetup:()=>{slot.document="setup";if(selectedCurrent() && !opening)navigate("setup");},
        showSettings:section=>{if(selectedCurrent()){settingsSection=section;navigate("settings");}},openExternal,
        revealFile:async path=>{if(!selectedCurrent())return;const view=slot.viewId;const file=relative(root,path).split("\\").join("/");if(view!==slot.viewId)return;
          if(new URL(window!.webContents.getURL()).pathname==="/desktop-app.html")post({type:"desktop/fileOpen",path:file});else shell.showItemInFolder(path);},
        openInspector:async kind=>{if(!selectedCurrent() || !inspectors)throw new Error("Return to this project before inspecting.");
          const source=selectedHost!.inspectorSource(),view=slot.viewId;
          await inspectors.open(kind,{...context,threadId:source.threadId},root,inspectorMemory(source.client),inspectorGraph(root,async path=>{
            const file=await containedPath(root,path);if(!selectedCurrent() || view!==slot.viewId)return;
            const value=relative(root,file).split("\\").join("/");window?.show();window?.focus();
            if(new URL(window!.webContents.getURL()).pathname==="/desktop-app.html")post({type:"desktop/fileOpen",path:value});else{pendingFile=value;await load("desktop-app");}
          },source.buildIndex));},
        readTheme:theme,saveTheme:async value=>{await meta.update("crucible.appearance",value.appearance);await meta.update("crucible.accent",value.accent);postTheme();},log,
        backendReady:url=>{slot.processId=randomUUID();if(selectedCurrent()){context.processId=slot.processId;post({type:"desktop/status",text:`Backend ${new URL(url).port} · checking project access`});}},
      });
      }catch(error){slots.delete(id);throw error;}
      pool.add(id,selectedHost);
    }
    if(!current())return;
    const selectedSlot=slots.get(id)!;if(threadId)selectedSlot.threadTarget=threadId;else delete selectedSlot.threadTarget;
    await pool.select(id,flushDraft,active=>attachProject(entry,active,generation),current);
    if(current())await publishProjects();
  }catch(error){if(current())await notice(error instanceof Error?error.message:"Project could not be opened.");}
  finally{if(openingLease===lease){openingLease=undefined;opening=false;}}
}
async function selectRecent(id:string,threadId?:string):Promise<void>{
  const entry=registry.get(id);
  try {const actual=await realpath(entry.root);if(actual!==entry.root || !(await stat(actual)).isDirectory())throw new Error();}
  catch {throw new Error("Project folder is missing or its identity changed. Use Locate to choose its folder again.");}
  await openProject(entry.root,threadId);
}
function sender(event: Electron.IpcMainInvokeEvent): void {
  if (!window) throw new Error("Desktop window is closed.");
  assertSender({ id: event.sender.id, frameIsMain: event.senderFrame === window.webContents.mainFrame, url: event.senderFrame?.url ?? "" }, window.webContents.id);
}
ipcMain.handle(CHANNEL.bootstrap, event => { if (lifecycleWindow?.owns(event.sender.id)) return lifecycleWindow.bootstrap(event); if (inspectors?.owns(event.sender.id)) return inspectors.bootstrap(event); sender(event); return { ...context }; });
ipcMain.handle(CHANNEL.command, async (event, raw: unknown) => {
  if (lifecycleWindow?.owns(event.sender.id)) return lifecycleWindow.handle(event, raw);
  if (inspectors?.owns(event.sender.id)) { if(closing)return {error:"Crucible is closing."}; return inspectors.handle(event, raw); }
  let validSender = false;
  const requestHost=host,requestView=context.viewId;
  const requestCurrent=()=>requestHost===host && requestView===context.viewId;
  try {
    sender(event); validSender = true;
    const envelope = parseEnvelope(raw, context, sequence); sequence = envelope.sequence;
    const payload = envelope.payload;
    if(provisioner.busy && !["themeReady","draft/flushAck","setup/preferences","desktop/lifecycleRead"].includes(payload.type))throw new Error("Runtime installation is in progress. Wait for it to finish, or quit to cancel it.");
    if(closing && !quitFlushCommand(payload.type))throw new Error("Crucible is closing. Wait for the draft save to finish.");
    if(payload.type==="desktop/update"){
      if(new URL(event.senderFrame!.url).pathname!=="/settings.html"||!updates)throw new Error("Open desktop Settings to manage updates.");
      const request=updateRequest.parse(payload);
      try{if(request.operation==="read")updates.read();
      else if(request.operation==="check")await updates.check();
      else if(request.operation==="download")await updates.download();
      else if(request.operation==="cancel")updates.cancel();
      else if(request.operation==="releases")await openExternal(updates.releases());
      else if(request.operation==="reveal")shell.showItemInFolder(await updates.reveal());
      else if(request.operation==="backup")shell.showItemInFolder(await updates.previous());
      else if(request.operation==="install"){
        if(request.version!==updates.read().version)throw new Error("The update selection changed. Review it again.");
        if(opening||provisioner.busy||desktopRuntimeStarting()||terminalLaunchPending||pool.summaries().some(({id})=>pool.get(id)?.restoreBusy()))throw new Error("Wait for project actions to finish before installing an update.");
        closing=true;
        try{const values=observations(await pool.refresh());if(values.some(p=>p.state!=="idle"||p.shells.length))throw new Error("Work is running or cannot be verified. Finish it before installing the update.");
          pendingUpdate=await updates.prepare();
          const fresh=observations(await pool.refresh());if(fresh.some(p=>p.state!=="idle"||p.shells.length))throw new Error("Project activity changed. Finish work before installing the update.");
          const transaction=pendingUpdate;
          installAfterCleanup=async()=>{try{await transaction.launch();}catch(error){await transaction.cancel();pendingUpdate=undefined;installAfterCleanup=undefined;throw error;}};
          await finishQuit();
        }catch(error){if(!cleanupStarted){await pendingUpdate?.cancel();pendingUpdate=undefined;closing=false;installAfterCleanup=undefined;}throw error;}
      }}catch(error){updates.error(error instanceof Error?error.message:"Update action failed.");}
      return{};
    }
    if(payload.type.startsWith("desktop/lifecycle")){
      if(new URL(event.senderFrame!.url).pathname!=="/settings.html")throw new Error("Change desktop preferences in Settings.");
      if(payload.type==="desktop/lifecycleRead"){lifecycleStatus();return{};}
      if(payload.type==="desktop/lifecycleSet"){
        const previous=lifecyclePrefs(),prefs=lifecyclePreferences.parse(payload.preferences);await meta.update("desktop.lifecycle",prefs);
        if(prefs.notifications==="off"){for(const n of notices.values())n.close();notices.clear();delivery="Notifications are off.";}
        else if(previous.notifications!==prefs.notifications)delivery="Enabled. Use Send test notification to check system delivery.";
        transitions.reset();updateLifecycle(await pool.refresh());lifecycleStatus();return{};
      }
      if(payload.type==="desktop/lifecycleTest"){
        if(lifecyclePrefs().notifications==="off")throw new Error("Enable notifications before sending a test.");
        showNotification({id:"test",name:"Crucible",owner:false,state:"idle",threads:[],shells:[]},"","Background notifications are enabled for Crucible.",true);return{};
      }
      throw new Error("Quit choices belong to the dedicated quit window.");
    }
    if(payload.type==="desktop/terminal"){
      if(new URL(event.senderFrame!.url).pathname!=="/desktop-app.html" || !requestHost || !context.projectId)throw new Error("Open a project conversation to use Terminal.");
      const {type:_type,...fields}=payload,request=terminalRequest.parse(fields),project=context.projectId,process=context.processId,entry=registry.get(project);
      const current=()=>requestCurrent() && process===context.processId && !closing;
      try {
        let result:unknown={};
        if(request.operation==="agentList" || request.operation==="agentRead"){
          if(request.threadId!==context.threadId)throw new Error("The selected conversation changed.");
          result=await requestHost.agentOutput(request.threadId!,request.operation==="agentRead"?request.agentSessionId:undefined);
        }else if(request.operation==="list")result={sessions:terminals.list(project)};
        else if(request.operation==="start"){
          if(terminalLaunchPending)throw new Error("A shell launch is already awaiting confirmation.");
          terminalLaunchPending=true;
          try {
            const lease=await requestHost.terminalLease();
            if(!current()||requestHost.restoreBusy())throw new Error("Project changed or file recovery is still finishing. Retry the shell launch afterward.");
            const choice=await dialog.showMessageBox(window!,{type:"warning",title:"Launch Your shell",message:`Launch your own shell in ${entry.name}?`,detail:`${entry.root}\n\n${lease.trusted?"This project session is trusted.":"This project is untrusted."} Your shell can edit files and run commands with your account's access. Its input is yours and does not use agent command approvals. Agent approvals, project trust and Full access remain unchanged. Closing its tab keeps it running; End session stops its process group.`,buttons:["Cancel","Launch shell"],defaultId:0,cancelId:0});
            if(choice.response!==1){if(current())post({type:"desktop/terminalResult",requestId:request.requestId,result:{}});return{};}
            if(!current() || !lease.current() || requestHost.restoreBusy())throw new Error("Project changed or file recovery is still finishing. Retry the shell launch afterward.");
            result={session:await terminals.start(project,entry.root,lease,request.cols,request.rows)};
          }finally{terminalLaunchPending=false;}
        }else if(request.operation==="read")result=terminals.read(project,request.sessionId!,request.offset!);
        else if(request.operation==="input" || request.operation==="interrupt")terminals.input(project,request.sessionId!,request.operation==="interrupt"?"\x03":request.input!);
        else if(request.operation==="resize")terminals.resize(project,request.sessionId!,request.cols!,request.rows!);
        else if(request.operation==="end")await terminals.end(project,request.sessionId!);
        else if(request.operation==="forget")terminals.forget(project,request.sessionId!);
        if(current())post({type:"desktop/terminalResult",requestId:request.requestId,result:terminalResult.parse(result)});
      }catch(error){if(current())post({type:"desktop/terminalResult",requestId:request.requestId,error:error instanceof Error?error.message:"Terminal action failed."});}
      return{};
    }
    if(payload.type==="desktop/projectsRead"){await publishProjects();return{};}
    if(payload.type==="desktop/selectProject"){await selectRecent(payload.projectId as string,payload.threadId as string|undefined);return{};}
    if(payload.type==="desktop/locateProject"){
      const previous=registry.get(payload.projectId as string);
      const selected=(await dialog.showOpenDialog(window!,{title:`Locate ${previous.name} — a different folder opens as a separate project`,properties:["openDirectory"]})).filePaths[0];
      if(selected && requestCurrent())await openProject(selected);return{};
    }
    if(payload.type==="desktop/removeProject"){
      const entry=registry.get(payload.projectId as string);
      if(pool.isSelected(entry.id))throw new Error("Switch to another project before removing this project.");
      if(payload.confirmed!==true || !requestCurrent())return{};
      await pool.remove(entry.id);DraftVault.remove(draftDirectory(),entry.id);registry.remove(entry.id);slots.delete(entry.id);await publishProjects();return{};
    }
    if(payload.type==="desktop/persistenceRead"){persistenceStatus();return{};}
    if(payload.type==="desktop/persistenceSet"){
      if(new URL(event.senderFrame!.url).pathname!=="/settings.html")throw new Error("Change draft storage in desktop Settings.");
      const enabled=payload.enabled===true;
      if(enabled && !DraftVault.available(safeStorage))throw new Error("Secure OS keychain storage is unavailable. Encrypted drafts remain off.");
      if(enabled){await flushDraft();pool.flush();}
      else if(draftReady)await draftFlusher.request(context.viewId,post);
      if(!requestCurrent())return{};
      if(!enabled)DraftVault.remove(draftDirectory());
      await meta.update("desktop.encryptedDrafts",enabled);
      for(const id of slots.keys())pool.get(id)?.persistenceChanged();
      persistenceStatus();return{};
    }
    if(payload.type==="desktop/recoverDraft"){
      if(new URL(event.senderFrame!.url).pathname!=="/settings.html" || !requestHost?.hasRecovery())throw new Error("No interrupted draft is waiting for recovery.");
      if(payload.confirmed===true && requestCurrent())requestHost.recoverDraft();persistenceStatus();return{};
    }
    if(payload.type==="desktop/importLegacyKeys"){
      if(new URL(event.senderFrame!.url).pathname!=="/settings.html" || context.projectId!==meta.get("desktop.legacyCredentialCandidate") || meta.get("desktop.legacyCredentialImport"))throw new Error("Previous keys are not available for this project. Enter credentials in Provider settings.");
      const entry=registry.get(context.projectId!);
      if(payload.confirmed!==true || !requestCurrent())return{};
      const digest=importLegacyCredentials(join(app.getPath("userData"),"secrets.json"),join(app.getPath("userData"),"projects",entry.id,"secrets.json"));
      await meta.update("desktop.legacyCredentialImport",{projectId:entry.id,digest});persistenceStatus();return{};
    }
    if (payload.type === "desktop/find" || payload.type === "desktop/findStop") {
      if (new URL(event.senderFrame!.url).pathname !== "/desktop-app.html" || !context.projectId || !finder) throw new Error("Find is available in the project conversation view.");
      if (payload.type === "desktop/findStop") finder.stop();
      else finder.search(payload as unknown as {requestId:string;query:string;forward:boolean;next:boolean});
      return {};
    }
    if(payload.type==="desktop/restore"){
      const request=restoreRequest.parse(payload),captured={viewId:context.viewId,projectId:context.projectId,threadId:context.threadId};
      if(new URL(event.senderFrame!.url).pathname!=="/desktop-app.html"||!requestHost||!captured.projectId||request.threadId!==captured.threadId)throw new Error("Open this conversation's Changes view to restore files.");
      try{const result=await requestHost.fileRestore(request.threadId,request.action);if(requestCurrent()&&context.threadId===captured.threadId)post(restoreReply.parse({type:"desktop/restoreResult",requestId:request.requestId,...captured,result}));}
      catch(error){if(requestCurrent()&&context.threadId===captured.threadId)post(restoreReply.parse({type:"desktop/restoreResult",requestId:request.requestId,...captured,error:error instanceof Error?error.message.slice(0,1024):"File restore was refused. Refresh before retrying."}));}
      return{};
    }
    if (payload.type.startsWith("desktop/baseline")) {
      if (new URL(event.senderFrame!.url).pathname !== "/desktop-app.html" || !context.projectId || !baselineRequests) throw new Error("Checkpoint inspection is available in the project conversation window.");
      await baselineRequests.handle(payload as BaselineRequest); return {};
    }
    if (payload.type.startsWith("desktop/git")) {
      if (new URL(event.senderFrame!.url).pathname !== "/desktop-app.html" || !context.projectId || !gitRequests) throw new Error("Git inspection is available in the project conversation window.");
      await gitRequests.handle(payload as GitRequest); return {};
    }
    if (payload.type.startsWith("desktop/files")) {
      if (new URL(event.senderFrame!.url).pathname !== "/desktop-app.html" || !context.projectId || !fileRequests) throw new Error("Files is available in the project conversation window.");
      if (["desktop/filesReveal", "desktop/filesCopyPath", "desktop/filesOpenEditor", "desktop/filesChooseEditor"].includes(payload.type)) {
        const captured = { viewId: context.viewId, projectId: context.projectId };
        try {
          const result = await fileActions!(payload.type as NativeFileAction, payload.path as string | undefined);
          if (context.viewId === captured.viewId) post({ type: "desktop/filesResult", requestId: payload.requestId, ...(context.projectId === captured.projectId ? { result } : { cancelled: true }) });
        } catch (error) { if (context.viewId === captured.viewId) post({ type: "desktop/filesResult", requestId: payload.requestId, error: error instanceof NativeEditorError ? error.message : "File action failed. Refresh the file or choose an editor again." }); }
      } else await fileRequests.handle(payload as WorkspaceFileRequest);
      return {};
    }
    if (payload.type === "desktop/layoutRead" || payload.type === "desktop/layoutSave") {
      if (new URL(event.senderFrame!.url).pathname !== "/desktop-app.html" || !context.projectId) throw new Error("Layout is available in the project conversation window.");
      const preferences = new LayoutPreferences(meta);
      if (payload.type === "desktop/layoutSave") await preferences.save(context.projectId, payload.layout as unknown as DesktopLayout);
      if(!requestCurrent())return{};
      post({ type: "desktop/layoutResult", requestId: payload.requestId, layout: preferences.read(context.projectId) });
      return {};
    }
    if (payload.type === "draft/flushAck") { draftFlusher.acknowledge(context.viewId, payload.requestId as string, payload.error as string | undefined); return {}; }
    if (payload.type === "draft/read") draftReady = true;
    if (payload.type === "switchThread" || payload.type === "newChat") {
      finder?.stop();
      if (host?.hasDraftSubmission()) throw new Error("Finish the pending send decision before changing conversations.");
      await flushDraft();
      if(!requestCurrent())throw new Error("Project view changed while saving.");
    }
    if (payload.type === "themeReady") { postTheme(); return {}; }
    if (payload.type === "desktop/openProject") { await openProject(); return {}; }
    if (payload.type === "desktop/openSetup") { if (host) await load("setup"); else await openProject(); return {}; }
    if (!host) throw new Error("Choose a project first.");
    const document = new URL(event.senderFrame!.url).pathname;
    await requestHost!.handle(payload, document === "/setup.html");
    if (!requestCurrent())return{};
    if (document === "/desktop-app.html" && payload.type === "webviewReady") {
      if (pendingFile) { post({type:"desktop/fileOpen",path:pendingFile}); pendingFile=undefined; }
      if (pendingShortcut) { post({type:"desktop/shortcut",action:pendingShortcut}); pendingShortcut=undefined; }
    }
    if (document === "/settings.html" && payload.type === "settings/load" && settingsSection) post({ type: "settings/navigate", section: settingsSection });
    return {};
  } catch (error) {
    const refused = raw && typeof raw === "object" ? (raw as { payload?: { type?: unknown; draftId?: unknown } }).payload : undefined;
    if (validSender && refused?.type === "sendMessage" && typeof refused.draftId === "string") requestHost?.rejectBoundaryDraft(refused.draftId);
    if(validSender && requestCurrent() && typeof refused?.type==="string"){
      const message=error instanceof Error?error.message:"Desktop action failed.";
      if(["desktop/selectProject","desktop/locateProject","desktop/removeProject"].includes(refused.type))post({type:"desktop/projectsError",message});
      if(["desktop/persistenceSet","desktop/recoverDraft","desktop/importLegacyKeys"].includes(refused.type))post({type:"desktop/persistenceError",message});
    }
    if(validSender && requestCurrent() && typeof refused?.type==="string" && ["desktop/trust","desktop/renameShared"].includes(refused.type))post({type:"desktop/accessError",message:error instanceof Error?error.message:"Project access action failed."});
    console.error("Desktop action refused:", error instanceof Error ? error.message : "Unknown error");
    // Re-poll ordinary gate cards as well as the preload's decisionRetry/draftRecovery.
    if (!(typeof refused?.type === "string" && refused.type.startsWith("desktop/baseline"))) void requestHost?.refreshAfterRefusal().catch(() => {});
    return { error: error instanceof Error ? error.message : "Desktop action refused." };
  }
});
app.on("before-quit", event => {
  if(closed)return;event.preventDefault();
  if(systemShutdown){void finishQuit();return;}
  if(closing)return;closing=true;projectOpenGeneration++;openingLease=undefined;opening=false;
  if(!lifecycleWindow){void finishQuit();return;}
  restoreMainWindow();
  void previewQuit().catch(async error=>{
    closing=false;
    // Recovery only: never turn a failed preview into silent forced discard.
    if(window)await dialog.showMessageBox(window,{type:"warning",title:"Quit preview unavailable",message:"Crucible kept running.",detail:String(error),buttons:["Keep running"],defaultId:0,cancelId:0});
  });
});
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", restoreMainWindow);
  void app.whenReady().then(async () => {
    // Electron 45 declarations omit the documented shutdown Event argument.
    (powerMonitor as import("node:events").EventEmitter).on("shutdown", (event?:Electron.Event) => {
      event?.preventDefault();systemShutdown=true;closing=true;projectOpenGeneration++;openingLease=undefined;opening=false;lifecycleWindow?.close();releaseAwake();
      // OS logout has a bounded grace period; no interactive preview can delay it forever.
      const deadline=setTimeout(()=>{console.error("OS shutdown grace expired; cleanup could not be verified.");closed=true;app.quit();},16000);
      deadline.unref();
      void finishQuit();
    });
    powerMonitor.on("suspend",()=>{suspended=true;transitions.reset();releaseAwake();});
    powerMonitor.on("resume",()=>{suspended=true;transitions.reset();void pool.refresh().then(values=>{suspended=false;updateLifecycle(values);});});
    meta = new JsonState(join(app.getPath("userData"), "preferences.json"));
    diagnose=localDiagnostics(app.getPath("userData"));diagnose("main-start");
    const currentApp=app.isPackaged&&process.platform==="darwin"?dirname(dirname(dirname(app.getPath("exe")))):undefined;
    updates=new DesktopUpdates({profile:app.getPath("userData"),resources:join(__dirname,"resources"),currentApp,currentVersion:app.getVersion(),target:`${process.platform}-${process.arch}`,bundleId:currentApp&&basename(currentApp)==="Crucible Preview.app"?"io.crucible.desktop.preview":"io.crucible.desktop",publish:state=>post({type:"desktop/updateStatus",state})});
    delivery=lifecyclePrefs().notifications==="off"?"Notifications are off.":"Enabled. System delivery has not been tested in this session.";
    if(meta.get("desktop.legacyCredentialCandidate")===undefined){
      const legacy=meta.get<string>("lastProject");let candidate:string|null=null;
      if(legacy){try{candidate=projectId(await realpath(legacy));}catch{}}
      await meta.update("desktop.legacyCredentialCandidate",candidate);
    }
    registry=new ProjectRegistry(join(app.getPath("userData"),"projects.json"),meta.get<string>("lastProject"));
    const dedicated = session.fromPartition("persist:crucible-desktop");
    await secureSession(dedicated, await realpath(join(__dirname, "renderer")));
    const savedWindow=restoredWindow(meta.get("desktop.window"),screen.getAllDisplays().map(d=>d.workArea));
    window = new BrowserWindow({ ...savedWindow.bounds, minWidth: Math.min(720,savedWindow.bounds.width), minHeight: Math.min(560,savedWindow.bounds.height), title: "Crucible", backgroundColor: "#181818",
      webPreferences: { ...WINDOW_SECURITY, preload: join(__dirname, "preload.cjs"), session: dedicated, devTools: !app.isPackaged },
    });
    if(savedWindow.maximized)window.maximize();
    windowMemory=rememberWindow(window,state=>meta.update("desktop.window",state),()=>diagnose("window-state-error"));
    inspectors = new Inspectors((kind, threadId) => new BrowserWindow({ parent: window!, width: 1080, height: 760, minWidth: 620, minHeight: 460, title: `${kind === "memory" ? "Crucible Memory Inspector" : "AXON · Dependency Space"} · ${threadId}`, backgroundColor: "#181818",
      webPreferences: { ...WINDOW_SECURITY, preload: join(__dirname, "preload.cjs"), session: dedicated, devTools: !app.isPackaged } }), themeMessage);
    lifecycleWindow=new LifecycleWindow(()=>new BrowserWindow({parent:window!,modal:true,width:740,height:560,minWidth:440,minHeight:420,show:false,title:"Quit Crucible",backgroundColor:nativeTheme.shouldUseDarkColors?"#181818":"#ffffff",
      webPreferences:{...WINDOW_SECURITY,preload:join(__dirname,"preload.cjs"),session:dedicated,devTools:!app.isPackaged}}),themeMessage,quitChoice);
    app.on("activate",restoreMainWindow);
    finder = new NativeFind(window.webContents,()=>context.viewId,post);
    window.webContents.on("found-in-page",(_event,result)=>finder?.result(result));
    window.webContents.on("did-start-navigation", (_event, _url, inPlace, mainFrame) => { if (mainFrame && !inPlace) { finder?.stop(); baselineRequests?.cancel(); gitRequests?.cancel(); fileRequests?.cancelSearch(); host?.abandonPreparedDraft(); context.viewId = randomUUID(); if(context.projectId){const slot=slots.get(context.projectId);if(slot)slot.viewId=context.viewId;} sequence = 0; draftReady = false; } });
    window.webContents.on("render-process-gone", (_event,details) => { diagnose("renderer-gone",{code:details.exitCode,reason:details.reason}); rendererAlive = false; baselineRequests?.cancel(); host?.abandonPreparedDraft(); });
    window.on("unresponsive", () => { diagnose("unresponsive"); rendererAlive = false; });
    window.on("responsive", () => { diagnose("responsive"); rendererAlive = true; });
    const reload = () => {
      if(closing)return;
      const focused = BrowserWindow.getFocusedWindow();
      if (focused && inspectors?.owns(focused.webContents.id)) { focused.webContents.reload(); return; }
      const url = new URL(window!.webContents.getURL());
      navigate(url.pathname.slice(1).replace(/\.html$/, ""));
    };
    const shortcut = (action: DesktopShortcut) => {
      if(closing)return;
      if (!host) { if (action==="open") void openProject(); return; }
      window?.show(); window?.focus();
      if (new URL(window!.webContents.getURL()).pathname==="/desktop-app.html") post({type:"desktop/shortcut",action});
      else { pendingShortcut=action; navigate("desktop-app"); }
    };
    window.webContents.on("before-input-event", (event, input) => {
      const action=desktopShortcutForKey(input,process.platform);
      if (action) { event.preventDefault(); if (input.type==="keyDown") shortcut(action); return; }
      const primary=process.platform==="darwin" ? input.meta && !input.control : input.control && !input.meta;
      if (primary && !input.alt && !input.shift && input.key.toLowerCase() === "r") { event.preventDefault(); if (input.type === "keyDown") reload(); }
    });
    window.on("focus", () => { void host?.refreshThreadList(); });
    window.on("close", event => { if (!closed) { event.preventDefault(); app.quit(); } });
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      ...(process.platform === "darwin" ? [{ role: "appMenu" as const }] : []),
      { label: "File", submenu: [...DESKTOP_SHORTCUTS.slice(0,3).map(item=>({label:item.label,accelerator:item.accelerator,click:()=>shortcut(item.action)})), { label: "Back to Chat", click: () => { if (host) navigate("desktop-app"); } }, { label: "Run Setup", click: () => { if (host) navigate("setup"); else void openProject(); } }, { role: "close" }] },
      { role: "editMenu" }, { label: "View", submenu: [...DESKTOP_SHORTCUTS.slice(3).map(item=>({label:item.label,accelerator:item.accelerator,click:()=>shortcut(item.action)})), { label: "Reload", accelerator: "CmdOrCtrl+R", click: reload }, { role: "toggleDevTools" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { role: "togglefullscreen" }] }, { role: "windowMenu" },
    ]));
    installTextMenu(window.webContents, window);
    nativeTheme.on("updated", postTheme);
    await load("desktop");
    projectsTimer=setInterval(()=>{if(!closing&&!suspended)void pool.poll().then(()=>{updateLifecycle();return publishProjects();}).catch(console.error);},5000);
    const initial = process.env.CRUCIBLE_DESKTOP_PROJECT ?? meta.get<string>("lastProject");
    if (initial) await openProject(initial);
  }).catch(error => { console.error("Desktop startup failed:", error instanceof Error ? error.message : "Unknown error"); app.quit(); });
}
