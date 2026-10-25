import { restoreAction, readRestoreResponse, type RestoreAction } from "./restore-protocol.js";
import { randomUUID } from "node:crypto";
import { readLifecycleReply } from "./lifecycle-protocol.js";
import { agentTranscript } from "./terminal-protocol.js";
import { parseProjectSession,ownerFor } from "../../vscode-extension/src/runtime/project-owner.js";
import { DraftVault } from "./draft-vault.js";
import type { ProjectActivity } from "./project-pool.js";
import { ThreadMetadata } from "./thread-metadata.js";
import { createGateActions } from "../../vscode-extension/src/gate-actions.js";
import { join, basename } from "node:path";
import { homedir } from "node:os";
import { createHash } from "node:crypto";
import { HttpBackendClient } from "@crucible/editor-client";
import { CrucibleController, type ControllerUI } from "../../vscode-extension/src/controller.js";
import { ChatView } from "../../vscode-extension/src/chat-view.js";
import { createControllerViewUI } from "../../vscode-extension/src/controller-view-ui.js";
import { routeChatMessage, type ChatMessageActions } from "../../vscode-extension/src/chat-message-router.js";
import { createComposerModelHost } from "../../vscode-extension/src/composer-model-host.js";
import { createHostSettingsDeps } from "../../vscode-extension/src/host-settings-deps.js";
import { createHostSetupDeps } from "../../vscode-extension/src/host-setup-deps.js";
import { createSettingsHandler, type SettingsInMsg } from "../../vscode-extension/src/settings-data.js";
import { createSetupHandler, type SetupInMsg } from "../../vscode-extension/src/setup-data.js";
import { BackendGate, normalizeBackendUrl } from "../../vscode-extension/src/backend-auth/backend-gate.js";
import { readBackendToken } from "../../vscode-extension/src/runtime/backend-token.js";
import { probeHealth } from "../../vscode-extension/src/runtime/probe-health.js";
import { isApprovalMode } from "../../vscode-extension/src/approval-mode.js";
import { DraftSession } from "../../vscode-extension/src/drafts/draft-session.js";
import { currentBaselineBackend } from "./baseline-host.js";
import { ThreadListPoller } from "./thread-list.js";
import { JsonState, EncryptedSecrets, type Encryption } from "./store.js";
import { createDesktopRuntime, assertInstalledLocalModels, INSTALL_NOTICE } from "./runtime.js";
import { projectFiles } from "./project-files.js";
import type { Payload } from "./contract.js";
import type { ThemeSettings } from "../../vscode-extension/src/theme-settings.js";
export interface HostPorts {
  draftPersistence(): boolean;
  provisionRuntime?: Parameters<typeof createDesktopRuntime>[1];
  canStartRuntime?: ()=>void;
  localTerminalBusy?(): boolean;
  takeThreadTarget?(): string | undefined;
  root: string; profile: string; resources: string; runtimeDir?: string; encryption: Encryption;
  openInspector?(kind: "memory" | "graph"): Promise<void>;
  post(message: Record<string, unknown>): void; viewIdentity(): unknown; isFocused?(): boolean;
  showChat(): void; showSetup(): void; showSettings(section?: string): void; isOpen(): boolean;
  openExternal(url: string): Promise<void>; revealFile(path: string): Promise<void>;
  readTheme(): ThemeSettings; saveTheme(theme: ThemeSettings): Promise<void>;
  log(text: string): void; backendReady(url: string): void;
}
export function createProjectHost(ports: HostPorts) {
  const { root } = ports;
  let disposed = false, restoring = false;
  let listPoller: ThreadListPoller | undefined;
  let selectedThread = "", lastTurnActive = false;
  const post = (message: Record<string, unknown>) => {
    if(message.type === "setup/preferences")message={...message,runtimePath:ports.runtimeDir ?? join(homedir(), ".crucible", "runtime")};
    if (message.type === "clearThread" && typeof message.threadId === "string") selectedThread = message.threadId;
    if (message.type === "renderThreadList" && typeof message.activeThreadId === "string") { selectedThread = message.activeThreadId; listPoller?.invalidate(); }
    if (message.type === "liveStatus") {
      message = { ...message, threadId: selectedThread };
      if (lastTurnActive && message.turnActive === false) void listPoller?.refresh(true);
      lastTurnActive = message.turnActive === true;
    }
    drafts.observe(message);
    ports.post(message);
  };
  let vault: DraftVault;
  const drafts = new DraftSession({ post, identity: ports.viewIdentity, admission: value => view.renderChatAdmission(value),
    changed: urgent => {if(!disposed)vault?.changed(() => drafts.exportState(), urgent);} });
  const rejectDraft = (id: string) => drafts.reject(id);
  const projectId = createHash("sha256").update(root).digest("hex");
  const state = new JsonState(join(ports.profile, "projects", projectId, "state.json"));
  const threadMetadata = new ThreadMetadata(state);
  const preferences = new JsonState(join(ports.profile, "projects", projectId, "preferences.json"));
  const readSecrets=()=>new EncryptedSecrets(new JsonState(join(ports.profile,"projects",projectId,"secrets.json")),ports.encryption);
  const secrets={get:(key:string)=>readSecrets().get(key),store:(key:string,value:string)=>readSecrets().store(key,value),delete:(key:string)=>readSecrets().delete(key)};
  const notice = (content: string) => post({ type: "appendMessage", message: { role: "agent", type: "notice", content, timestamp: new Date().toISOString(), metadata: {} } });
  vault = new DraftVault(join(ports.profile,"drafts"),projectId,ports.encryption,ports.draftPersistence, message => post({type:"desktop/draftError",message}));
  let draftError: string | undefined;
  try { const saved=vault.read(); if (saved !== undefined) drafts.importState(saved); }
  catch(error) { vault.invalidate(); draftError=error instanceof Error?error.message:"Saved drafts could not be restored."; }
  let backendURL = "", projectTrusted=false, handling=0, accessReads=0, backendGeneration=0;

  const runtime = createDesktopRuntime({ clientName:"Crucible Desktop",runtimeDir: ports.runtimeDir ?? join(homedir(), ".crucible", "runtime"), resourceDir: ports.resources,
    state, secrets, configuration: () => preferences, updateGlobalSetting: (key, value) => preferences.update(key, value),
    output: { append: ports.log, appendLine: text => ports.log(`${text}\n`), show: () => notice("Runtime logs are in the desktop profile's runtime.log file.") },
    statusBar: { set text(text: string) { post({ type: "desktop/status", text }); }, show() {}, hide() {} },
    authStatus: { text: "", show: () => notice("Backend authorization failed. Reopen the project after checking the runtime logs."), hide() {} },
    showError: async message => { notice(message); return undefined; },
  },ports.provisionRuntime,ports.canStartRuntime);
  const gate = new BackendGate({ fetch: (input, init) => fetch(input, init), readToken: readBackendToken,
    probe: port => probeHealth(port, { readToken: readBackendToken, fetchRaw: async (url, init) => { const response = await fetch(url, { ...init, redirect: "manual" }); return { status: response.status, body: await response.text() }; } }),
    now: Date.now, report: (_base, ok, reason) => runtime.setAuthError(ok ? null : reason ?? "Unauthorized"),
  });
  const readAccess=async(publish=true)=>{
    const base=backendURL, read=++accessReads, generation=backendGeneration;
    if(!base){projectTrusted=false;post({type:"desktop/access",state:"unavailable",workspace:root,message:"Project backend is not connected."});return;}
    const capturedOwner=ownerFor(base);
    const current=()=>base===backendURL && generation===backendGeneration && read===accessReads && capturedOwner===ownerFor(base);
    try {
      const response=await gate.authedFetchFor(base)(`${base}/v1/project/session`,{signal:AbortSignal.timeout(3000)});
      if(!response.ok)throw new Error("Runtime update required for project ownership. Inspection is read only.");
      const access=parseProjectSession(await response.json());
      if(!current())return;
      if(access.workspace!==root)throw new Error("This backend belongs to a different project.");
      projectTrusted=access.role==="owner" && !!ownerFor(backendURL) && access.trusted;
      if(publish)post({type:"desktop/access",state:access.role,workspace:root,processId:access.process_id,pid:access.pid,client:access.client_name,trusted:projectTrusted,message:access.role==="observer"?"Read only. Use the controlling client, or close it and reopen this project here.":"This client controls the project. Trust never selects Full access automatically."});
      return access;
    }catch(error){if(!current())return;projectTrusted=false;post({type:"desktop/access",state:"unavailable",workspace:root,message:error instanceof Error?error.message:"Ownership is unavailable."});return;}
  };
  const clientFactory = (url: string) => { const { base } = normalizeBackendUrl(url); return new HttpBackendClient({ baseUrl: base, fetchFn: gate.fetchFor(base), authToken: gate.tokenFor(base) }); };
  let controller: CrucibleController;
  const models = createComposerModelHost({ client: () => controller.configClient(), runtime,
    refreshContextUsage: () => controller.refreshContextUsage(), refreshModels: () => view.refreshModels() });
  const view = new ChatView({ postMessage: post, isOpen: ports.isOpen, identity: ports.viewIdentity,
    createRequestId: () => crypto.randomUUID(), listModels: () => models.state() });
  listPoller = new ThreadListPoller({ list: () => controller.configClient().listChatThreads(root), activeThread: () => selectedThread,
    focused: () => ports.isFocused?.() ?? true, identity: () => `${backendURL}|${String(ports.viewIdentity())}`,
    render: (threads, active) => view.renderThreadList(threads, active), log: ports.log });
  const files = projectFiles(root, runtime.runtimeDir, ports.revealFile, notice, post);
  const ui: ControllerUI = { ...createControllerViewUI(view), getWorkspacePath: () => root,
    restoreDraft: (text, recovery) => { if (!recovery || !rejectDraft(recovery.draftId)) view.restoreDraft(text, recovery); },
    staleDraft: (_text, recovery) => { if (recovery) rejectDraft(recovery.draftId); },
    renderChatAdmission: value => drafts.renderAdmission(value),
    draftCreated: (id, threadId) => drafts.created(id, threadId),
    draftAccepted: (id, threadId) => drafts.accepted(id, threadId),
    promptForGoal: async () => undefined, promptForTaskId: async () => undefined, promptForRejectReason: async () => undefined,
    promptForResumeStage: async () => undefined, promptForMaxIterationsOverride: async () => undefined, promptForScopeDecision: async () => undefined,
    showInfo: notice, showWarning: notice, showError: notice, promptSetup: message => { notice(message); ports.showSetup(); }, openChatPanel: ports.showChat,
  };
  controller = new CrucibleController(clientFactory, {
    load: async () => state.get("session", null), save: async session => state.update("session", session), clear: async () => state.update("session", undefined),
  }, { getBackendBaseUrl: () => backendURL, getDefaultMode: () => "project_edit", getPollIntervalMs: () => 1000 }, ui,
  { openDiff: async () => notice("Diff view arrives with the desktop workspace panel in the next UI checkpoint.") });
  controller.configureApproval({
    savedMode: () => { const mode = state.get("crucible.approvalMode"); return isApprovalMode(mode) ? mode : undefined; },
    reviewOn: () => runtime.getStepReview(), saveMode: async mode => { await state.update("crucible.approvalMode", mode); await runtime.setStepReview(mode === "ask"); },
    warningDismissed: () => state.get("crucible.fullAccessWarningDismissed", false), saveWarningDismissed: () => state.update("crucible.fullAccessWarningDismissed", true),
    isTrusted: () => projectTrusted, firstSendAcknowledged: () => state.get("crucible.fullAccessFirstSendAcknowledged", false),
    acknowledgeFirstSend: () => state.update("crucible.fullAccessFirstSendAcknowledged", true), confirmFirstFullAccessSend: () => view.confirmFullAccessFirstSend(basename(root)),
  });
  controller.onBackendReachable(() => { void controller.pushPreferences({ autoAccept: !runtime.getStepReview(), planMode: runtime.getPlanMode() }); });
  controller.onAttention(thread => ports.log(`[attention] Thread ${thread} needs a decision.\n`));
  runtime.onBackendReady(() => { ++backendGeneration; backendURL = runtime.backendUrl(root) ?? ""; ports.backendReady(backendURL); void readAccess(); });
  const settingsDeps = createHostSettingsDeps({ runtimeManager: runtime, workspacePath: root, clientFactory,
    resolveBackendUrl: () => backendURL, openExternal: ports.openExternal, native: {
      readAppearance: ports.readTheme, setAppearance: ports.saveTheme,
      readEnvFlags: () => Object.fromEntries(Object.entries({ "crucible.policy.shell": "ask", "crucible.policy.scope": "ask", "crucible.webSearch.enabled": true, "crucible.webFetch.enabled": true, "crucible.memory.enabled": true, "crucible.localModels": runtime.localModels() }).map(([key, fallback]) => [key, String(key === "crucible.policy.shell" ? "ask" : preferences.get(key, fallback))])),
      updateSetting: async (key, value) => { if (key === "crucible.localModels") { const enabled = value === true || value === "true"; assertInstalledLocalModels(runtime, enabled); await runtime.setLocalModels(enabled); } else await preferences.update(key, value); },
      readInstructions: files.readInstructions, writeInstructions: async (...args) => {
        const access = await readAccess(false);
        if (access?.role !== "owner" || !ownerFor(backendURL)) throw new Error("Read only. Save project instructions in the controlling client.");
        return files.writeInstructions(...args);
      },
      openFile: async path => {
        // Global agent files are allowed only when the backend catalog names that exact file.
        try { await files.open(path); } catch {
          const catalog = await controller.configClient().listAgentDefinitions();
          if (!catalog.agents.some(agent => agent.path === path)) throw new Error("This file is not in the project or agent catalog.");
          await ports.revealFile(path);
        }
      },
    } });
  const settingsHandler = createSettingsHandler(settingsDeps, post, models.afterSettingsMutation);
  const setupHandler = createSetupHandler(createHostSetupDeps({ runtime, workspace: root, clientFactory,
    openChat: ports.showChat, onBackendUrl: url => { if(url!==backendURL)++backendGeneration;backendURL = url; }, openExternal: ports.openExternal,
    install: async (progress, localModels = true) => {
      if(ports.provisionRuntime){await runtime.setLocalModels(localModels);return runtime.install(progress);}
      if (!runtime.isInstalled()) throw new Error(INSTALL_NOTICE);
      if ((runtime.installedRuntime()?.localModels ?? true) !== localModels) throw new Error(INSTALL_NOTICE);
      await runtime.setLocalModels(localModels); progress({ id: "runtime", status: "done", detail: "Using the installed runtime; no shared packages were changed." }); return { ok: true };
    }, start: async () => backendURL ? { port: Number(new URL(backendURL).port), reused: false } : runtime.startForWorkspace(root),
  }), post);
  const gateActions = createGateActions({ controller, drafts, post, setPlanMode: async (enabled, isCurrent = () => true) => {
    const client = controller.configClient();
    if (!isCurrent()) return;
    await runtime.setPlanMode(enabled);
    if (!isCurrent()) return;
    await client.setPlanMode(enabled).catch(() => undefined);
  } });
  const actions: ChatMessageActions = {
    onGateDecision: action => gateActions.submit(action),
    onCheckChatReceipt: () => controller.checkChatReceipt(),
    onMessage: (...args) => drafts.send(args[6], () => controller.sendChatMessage(...args)), onNewChat: () => drafts.navigate(() => controller.newChatThread(), false), onSwitchThread: id => drafts.navigate(() => controller.switchChatThread(id), false),
    onViewDiffFile: async () => notice("Diff view arrives with the desktop workspace panel in the next UI checkpoint."),
    onCommandDecision: (...args) => controller.handleCommandDecisionFromChat(...args), onMcpDecision: (...args) => controller.handleMcpDecisionFromChat(...args),
    onModeDecision: (...args) => controller.handleModeDecisionFromChat(...args), onClarifyDecision: (...args) => controller.handleClarifyDecisionFromChat(...args),
    onEditDecision: (...args) => controller.handleEditDecisionFromChat(...args), onStopTurn: () => controller.stopActiveTurn(), onSetReviewPref: value => controller.setReviewPref(value),
    onListPrompts: () => controller.listPrompts(), onExpandPrompt: (...args) => controller.expandPrompt(...args), onListSkills: () => controller.listSkills(),
    onReady: async () => {
      const identity=ports.viewIdentity();
      post({type:"desktop/project",name:basename(root)});await controller.openChat();
      if(identity!==ports.viewIdentity() || !ports.isOpen())return;
      const target=ports.takeThreadTarget?.();
      if(target)await drafts.navigate(()=>controller.switchChatThread(target),false);
      listPoller?.start();
    }, onListModels: () => models.state(), onSetModel: (...args) => models.setModel(...args), onSetReasoningEffort: value => models.setReasoningEffort(value),
    onOpenSettings: ports.showSettings, onOpenMemoryPanel: () => ports.openInspector?.("memory"), onOpenGraphPanel: () => ports.openInspector?.("graph"),
    onListWorkspaceFiles: files.inventory, onOpenFile: path => { void files.open(path).catch(error => notice(String(error))); }, onFetchSessionTranscript: id => controller.fetchSessionTranscript(id),
    onGetPlanMode: () => runtime.getPlanMode(), onSetPlanMode: enabled => gateActions.setPlanMode(enabled),
    onGetStepReview: () => runtime.getStepReview(), onRewindPreview: id => controller.previewRewind(id), onRewindConfirm: id => controller.rewindTo(id),
    onSetOpenAgents: ids => controller.setOpenAgents(ids), onStopAgent: id => controller.stopAgent(id), onStopAllAgents: () => controller.stopAllAgents(),
    onSetOpenTeams: ids => controller.setOpenTeams(ids), onDisbandTeam: id => controller.disbandTeam(id), onTeamPlanDecision: (...args) => controller.decideTeamPlan(...args),
  };
  let outputReading=false;
  let activityRead: Promise<ProjectActivity> | undefined;
  const activity = (): Promise<ProjectActivity> => {
    if (activityRead) return activityRead;
    const actual = (async (): Promise<ProjectActivity> => {
      const localBusy = restoring || !!ports.localTerminalBusy?.() || handling > 0 || drafts.hasSubmission || !controller.canDetachProjectView();
      if (!backendURL) return { state: localBusy ? "busy" : "idle", attention: [] };
      const base = backendURL, generation = backendGeneration, owner = ownerFor(base), nonce = randomUUID();
      const before = await readAccess(false);
      const response = await gate.authedFetchFor(base)(`${base}/v1/chat/lifecycle?workspace=${encodeURIComponent(root)}&request_id=${nonce}`, {signal:AbortSignal.timeout(4000)});
      if (!response.ok) return {state:"unknown",attention:[]};
      const active = await readLifecycleReply(response);
      const after = await readAccess(false);
      if (disposed || !active.supported || active.request_id !== nonce || active.requested_workspace !== root || active.workspace_path !== root ||
        base !== backendURL || generation !== backendGeneration || owner !== ownerFor(base) || !before || !after ||
        before.process_id !== after.process_id || before.pid !== after.pid) return {state:"unknown",attention:[]};
      if (new Set(active.threads.map(t=>t.thread_id)).size !== active.threads.length) return {state:"unknown",attention:[]};
      const attention = active.threads.filter(t=>t.gates>0).map(t=>({threadId:t.thread_id,title:t.title,gates:t.gates}));
      const busy = localBusy || active.threads.some(t=>t.turn_active || t.agents.length || t.teams.length || t.sessions.length || t.gates);
      return {state:busy?"busy":"idle",attention,evidence:{identity:`${generation}:${after.process_id}:${after.pid}:${active.process_id}`,owner:after.role==="owner" && !!owner,threads:active.threads}};
    })().catch(():ProjectActivity=>({state:"unknown",attention:[]}));
    // A visible deadline never releases the physical request lease.
    const visible = Promise.race([actual,new Promise<ProjectActivity>(resolve=>{const timer=setTimeout(()=>resolve({state:"unknown",attention:[]}),12000);void actual.finally(()=>clearTimeout(timer));})]);
    activityRead = visible;
    void actual.finally(()=>{if(activityRead===visible)activityRead=undefined;});
    return visible;
  };
  const canDetach=()=>!restoring && handling===0 && !drafts.hasSubmission && controller.canDetachProjectView();
  const fileRestore=async(threadId:string,raw:RestoreAction)=>{
    const action=restoreAction.parse(raw);
    if(disposed||restoring||!canDetach()||ports.localTerminalBusy?.())throw new Error("Finish project actions and end Your shell before using file recovery.");
    if(!threadId||threadId!==controller.memoryThreadId())throw new Error("The conversation changed. Reopen its Changes view.");
    restoring=true;
    const base=backendURL,generation=backendGeneration,identity=ports.viewIdentity(),owner=base?ownerFor(base):undefined;
    const current=()=>!disposed && base===backendURL && generation===backendGeneration && identity===ports.viewIdentity() && threadId===controller.memoryThreadId() && !!base && owner===ownerFor(base);
    try{
      const access=await readAccess(false);
      if(!current()||!owner||access?.role!=="owner"||ports.localTerminalBusy?.())throw new Error("Only the current controlling client can restore files, with Your shell ended.");
      const response=await gate.authedFetchFor(base)(`${base}/v1/chat/threads/${encodeURIComponent(threadId)}/file-restore`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({...action,process_id:access.process_id}),signal:AbortSignal.timeout(15000)});
      const result=await readRestoreResponse(response);
      if(!current())throw new Error("The view changed. Check this conversation's recovery history before retrying.");
      return result;
    }catch(error){
      if(error instanceof Error && (error.name==="TimeoutError"||error.name==="AbortError"||error instanceof TypeError))throw new Error("File recovery could not confirm completion. Check recovery history before trying again.");
      throw error;
    }finally{restoring=false;}
  };
  return { projectId, runtime, activity, canDetach, fileRestore, restoreBusy:()=>restoring,
    terminalLease: async () => {
      if(restoring)throw new Error("Wait for file recovery to finish before launching Your shell.");
      const base=backendURL,generation=backendGeneration,owner=base?ownerFor(base):undefined;
      const access=await readAccess(false);
      if(!owner || !access || access.role!=="owner" || base!==backendURL || generation!==backendGeneration || ownerFor(base)!==owner)throw new Error("Only this project's controlling client can launch Your shell. Agent output remains read only.");
      return {trusted:access.trusted,current:()=>base===backendURL && generation===backendGeneration && ownerFor(base)===owner};
    },
    agentOutput: async (threadId:string,sessionId?:string) => {
      if(outputReading)throw new Error("Agent output read is already in progress.");
      if(!threadId || threadId!==controller.memoryThreadId() || !backendURL)throw new Error("Open this conversation before inspecting agent output.");
      const base=backendURL,generation=backendGeneration,identity=ports.viewIdentity();
      const current=()=>base===backendURL && generation===backendGeneration && identity===ports.viewIdentity() && threadId===controller.memoryThreadId();
      outputReading=true;
      try {
        let result;
        if(sessionId){
          const response=await gate.authedFetchFor(base)(`${base}/v1/chat/threads/${encodeURIComponent(threadId)}/sessions/${encodeURIComponent(sessionId)}/transcript`,{signal:AbortSignal.timeout(4000)});
          result={transcript:response.status===404?null:response.ok?agentTranscript.parse(await response.json()):null};
        }else {const live=await controller.configClient().getThreadLiveState(threadId,AbortSignal.timeout(4000));result={agents:(live.sessions??[]).slice(0,128)};}
        if(!current())throw new Error("Agent output changed with the conversation. Refresh its current session.");
        return result;
      }finally{outputReading=false;}
    },
    hasRecovery: () => drafts.hasRecovery, recoverDraft: () => {drafts.recover();vault.flush();},
    detach: () => { if (!canDetach()) throw new Error("Finish the current project action before switching."); if(backendURL)controller.detachProjectView(); drafts.resetView(); },
    flush: () => vault.flush(),
    persistenceChanged: () => { vault.reset(); if(ports.draftPersistence())vault.changed(()=>drafts.exportState(),true); },
    pause: async () => {
      if (!canDetach() || (await activity()).state!=="idle" || !ownerFor(backendURL)) return false;
      const paused=await runtime.pause(root); if(paused){backendURL="";projectTrusted=false;}return paused;
    }, inspectorSource: () => ({ client: controller.configClient(), threadId: controller.memoryThreadId(), root,
      buildIndex: () => controller.buildDependencyIndex(() => requestProjectIndex(gate, backendURL, root)) }), baselineBackend: currentBaselineBackend(() => controller.configClient()), rejectDraft, hasDraftSubmission: () => drafts.hasSubmission,
    rejectBoundaryDraft: (id: string) => drafts.rejectPrepared(id),
    abandonPreparedDraft: () => drafts.resetView(),
    start: async () => { if (!runtime.isInstalled() || !await runtime.getProviderSettings()) { ports.showSetup(); return; } if(!backendURL)await runtime.startForWorkspace(root); ports.showChat(); },
    handle: async (payload: Payload, setup: boolean) => {
      // Accepted stream bodies outlive the attached view. Their preparation,
      // pending/unknown receipt and work-start are fenced by drafts/controller.
      if(disposed)throw new Error("This project is closing.");
      if(restoring)throw new Error("Wait for file recovery to finish before another project action.");
      const blocking=!["sendMessage","modeDecision","clarifyDecision"].includes(payload.type);
      if(blocking)++handling;
      try {
      if(payload.type==="desktop/accessRead"){await readAccess();return;}
      if(payload.type==="desktop/trust"){
        const current=await readAccess(false);
        if(!current || current.role!=="owner" || current.process_id!==payload.processId)throw new Error("Project ownership changed. Reopen project access before changing trust.");
        const response=await gate.authedFetchFor(backendURL)(`${backendURL}/v1/project/trust`,{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify({process_id:current.process_id,trusted:payload.trusted}),signal:AbortSignal.timeout(5000)});
        if(!response.ok){const result=await response.json().catch(()=>({}));throw new Error(typeof result.detail==="string"?result.detail:"Trust was not changed. Retry while the project is idle.");}
        await readAccess();await controller.approvalTrustGranted();return;
      }
      if(payload.type==="desktop/renameShared"){
        const current=await readAccess(false);if(!current || current.role!=="owner")throw new Error("Only the controlling client can rename shared conversations.");
        const response=await gate.authedFetchFor(backendURL)(`${backendURL}/v1/chat/threads/${encodeURIComponent(payload.threadId as string)}/title`,{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify({title:payload.title}),signal:AbortSignal.timeout(5000)});
        if(!response.ok)throw new Error("Conversation title was not changed. Reopen it and retry.");
        await listPoller?.refresh(true);post({type:"desktop/sharedRenamed",threadId:payload.threadId});return;
      }
      if(payload.type==="webviewReady"){void readAccess();if(draftError)post({type:"desktop/draftError",message:draftError});}
      if (drafts.handle(payload)) return;
      if (payload.type === "desktop/threadsRead" || payload.type === "desktop/threadsUpdate") {
        try {
          const { type: _type, requestId, ...action } = payload;
          const metadata = payload.type === "desktop/threadsRead" ? threadMetadata.read() : await threadMetadata.change(action);
          post({ type: "desktop/threadsResult", requestId, metadata });
        } catch (error) { post({ type: "desktop/threadsResult", requestId: payload.requestId, error: String(error) }); }
        return;
      }
      const ownsSubmission = drafts.dispatch(payload);
      if (setup) { await setupHandler(payload as unknown as SetupInMsg); return; }
      if (payload.type.startsWith("settings/")) { await settingsHandler(payload as unknown as SettingsInMsg); return; }
      if (await files.bridge.handle(payload)) return;
      try { await routeChatMessage(payload, actions, { postMessage: post, replayState: () => view.replayState(), answerFullAccessQuestion: (...args) => view.answerFullAccessQuestion(...args),
        setApprovalMode: (...args) => controller.setApprovalMode(...args), refreshModels: () => view.refreshModels(), showError: notice }); }
      catch (error) { if (ownsSubmission) rejectDraft(payload.draftId as string); throw error; }
      } finally { if(blocking)--handling; }
    },
    refreshThreadList: () => listPoller?.refresh(true),
    refreshAfterRefusal: () => controller.pollThreadLiveState(),
    dispose: async (discardDrafts = false) => {
      // Persistence is a preflight: failure leaves the controller, drafts and
      // runtime intact so Cancel/retry can recover exact unsaved bytes.
      if(discardDrafts)vault.discardPending();
      else {try{vault.dispose();}catch(error){throw new AggregateError([error],"Project draft cleanup failed. Drafts and work remain available; retry saving or explicitly discard unsaved edits.");}}
      disposed = true; const errors: unknown[] = [];
      for (const clean of [() => listPoller?.dispose(), () => controller.dispose(),
        () => view.disposeViewState(), () => files.dispose(), () => drafts.dispose()]) {
        try { clean(); } catch (error) { errors.push(error); }
      }
      try { await runtime.dispose(); } catch (error) { errors.push(error); }
      if (errors.length) throw new AggregateError(errors, "Project cleanup failed. Retry quitting."); },
  };
}

export async function requestProjectIndex(gate: BackendGate, base: string, root: string): Promise<void> {
  const response = await gate.authedFetchFor(base)(`${base}/v1/index/build`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace_path: root }), signal: AbortSignal.timeout(15000),
  });
  void response.body?.cancel().catch(() => {});
  if (!response.ok) throw new Error(`Index build failed (${response.status}).`);
}
