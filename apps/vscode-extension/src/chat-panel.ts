import { createGateActions } from "./gate-actions.js";
import { ChatView } from "./chat-view.js";
import { DraftSession } from "./drafts/draft-session.js";
import type { ChatAdmissionView } from "./controller.js";
import type {
  ChatMessageHandler,
  PlanCardActionHandler,
  InlineChangeActionHandler,
  ViewDiffFileHandler,
  NewChatHandler,
  SwitchThreadHandler,
  ScopeDecisionHandler,
  ValidationDecisionHandler,
  CommandDecisionHandler,
  StepDecisionHandler,
  ModeDecisionHandler,
  ClarifyDecisionHandler,
  EditDecisionHandler,
  AcceptTaskHandler,
  RejectTaskHandler,
  ResumeTaskHandler,
  StopTurnHandler,
  AbortTaskHandler,
  SetReviewPrefHandler,
  ListPromptsHandler,
  ExpandPromptHandler,
  ListSkillsHandler,
  McpDecisionHandler,
  ComposerModelState,
  ListModelsHandler,
  SetModelHandler,
  SetReasoningEffortHandler,
  OpenSettingsHandler,
  OpenMemoryPanelHandler,
  OpenGraphPanelHandler,
  ListWorkspaceFilesHandler,
  FetchSessionTranscriptHandler,
  OpenFileHandler,
  GetPlanModeHandler,
  GetStepReviewHandler,
  RewindPreviewHandler,
  RewindConfirmHandler,
  SetPlanModeHandler
} from "./chat-message-handlers.js";
export type {
  ChatMessageHandler,
  PlanCardActionHandler,
  InlineChangeActionHandler,
  ViewDiffFileHandler,
  NewChatHandler,
  SwitchThreadHandler,
  ScopeDecisionHandler,
  ValidationDecisionHandler,
  CommandDecisionHandler,
  StepDecisionHandler,
  ModeDecisionHandler,
  ClarifyDecisionHandler,
  EditDecisionHandler,
  AcceptTaskHandler,
  RejectTaskHandler,
  ResumeTaskHandler,
  StopTurnHandler,
  AbortTaskHandler,
  SetReviewPrefHandler,
  ListPromptsHandler,
  ExpandPromptHandler,
  ListSkillsHandler,
  McpDecisionHandler,
  ComposerModelState,
  ListModelsHandler,
  SetModelHandler,
  SetReasoningEffortHandler,
  OpenSettingsHandler,
  OpenMemoryPanelHandler,
  OpenGraphPanelHandler,
  ListWorkspaceFilesHandler,
  FetchSessionTranscriptHandler,
  OpenFileHandler,
  GetPlanModeHandler,
  GetStepReviewHandler,
  RewindPreviewHandler,
  RewindConfirmHandler,
  SetPlanModeHandler
} from "./chat-message-handlers.js";
import { routeChatMessage, type ChatMessageActions, type ChatMessageView } from "./chat-message-router.js";
import { randomUUID } from "node:crypto";
import * as fs from "node:fs";
import * as vscode from "vscode";
import type { SettingsInMsg, SettingsOutMsg } from "./settings-data.js";
import type { FileReferenceBridge, FileReferenceCatalogMessage } from "./file-references/bridge.js";
import { injectThemeAttributes, themeHub, themeReadyReply } from "./theme-settings.js";

// Builds a settings message handler bound to a poster into THIS webview — the chat
// bundle now embeds the settings UI (floating overlay), so its settings/* messages
// are handled here and replies posted back into the same webview. See settings-deps.ts.
export type SettingsHandlerFactory = (
  post: (msg: SettingsOutMsg) => void,
) => (msg: SettingsInMsg) => void | Promise<void>;

export class ChatPanel extends ChatView {
  private panel: vscode.WebviewPanel | null = null;
  private draftGeneration: unknown = {};
  private readonly drafts = new DraftSession({
    post: message => { void this.panel?.webview.postMessage(message); },
    identity: () => this.panel ? this.draftGeneration : null,
    admission: value => this.renderChatAdmission(value),
  });
  renderDraftAdmission(value: ChatAdmissionView | null): void { this.drafts.renderAdmission(value); }
  draftCreated(id: string, threadId: string): void { this.drafts.created(id, threadId); }
  draftAccepted(id: string, threadId: string): void { this.drafts.accepted(id, threadId); }
  rejectDraft(id: string): boolean { return !!this.drafts.reject(id); }
  override restoreDraft(text: string, recovery?: Parameters<ChatView["restoreDraft"]>[1]): void {
    if (!recovery || !this.rejectDraft(recovery.draftId)) super.restoreDraft(text, recovery);
  }
  newChat(): Promise<void> { return this.drafts.navigate(() => this.onNewChat()); }
  switchThread(threadId: string): Promise<void> { return this.drafts.navigate(() => this.onSwitchThread(threadId)); }

  private receiptCheckHandler: () => Promise<void> = async () => {};
  setReceiptCheckHandler(handler: () => Promise<void>): void { this.receiptCheckHandler = handler; }
  private approvalHandler: ((mode: unknown, confirmed: boolean, remember: boolean) => Promise<void>) | null = null;

  setApprovalHandler(handler: (mode: unknown, confirmed: boolean, remember: boolean) => Promise<void>): void {
    this.approvalHandler = handler;
  }
  private themeSubscription: (() => void) | null = null;
  private fileReferenceBridge: FileReferenceBridge | null = null;

  setFileReferenceBridge(bridge: FileReferenceBridge): void {
    this.fileReferenceBridge = bridge;
  }

  postFileReferenceCatalog(message: FileReferenceCatalogMessage): void {
    void this.panel?.webview.postMessage(message);
  }
  // Settings handler for the embedded floating settings overlay. The factory is
  // injected by extension.ts (it needs clientFactory, defined after this panel is
  // constructed); the concrete handler is (re)built per webview mount in
  // registerHandlers so it can post replies into the live panel.
  private settingsHandlerFactory: SettingsHandlerFactory | null = null;
  private settingsHandle: ((msg: SettingsInMsg) => void | Promise<void>) | null = null;

  private gateActions?: ReturnType<typeof createGateActions>;
  configureGateActions(controller: import("./controller.js").CrucibleController): void {
    this.gateActions = createGateActions({ controller, drafts: this.drafts, setPlanMode: this.onSetPlanMode, post: message => { this.drafts.observe(message); void this.panel?.webview.postMessage(message); } });
  }

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly onMessage: ChatMessageHandler,
    private readonly onPlanAction: PlanCardActionHandler,
    private readonly onNewChat: NewChatHandler,
    private readonly onSwitchThread: SwitchThreadHandler,
    private readonly onApplyInlineChange: InlineChangeActionHandler,
    private readonly onDiscardInlineChange: InlineChangeActionHandler,
    private readonly onViewDiffFile: ViewDiffFileHandler,
    private readonly onScopeDecision: ScopeDecisionHandler,
    private readonly onValidationDecision: ValidationDecisionHandler,
    private readonly onCommandDecision: CommandDecisionHandler,
    private readonly onStepDecision: StepDecisionHandler,
    private readonly onModeDecision: ModeDecisionHandler,
    private readonly onClarifyDecision: ClarifyDecisionHandler,
    private readonly onEditDecision: EditDecisionHandler,
    private readonly onAcceptTask: AcceptTaskHandler,
    private readonly onRejectTask: RejectTaskHandler,
    private readonly onResumeTask: ResumeTaskHandler,
    private readonly onStopTurn: StopTurnHandler,
    private readonly onAbortTask: AbortTaskHandler,
    private readonly onSetReviewPref: SetReviewPrefHandler,
    private readonly onListPrompts: ListPromptsHandler,
    private readonly onExpandPrompt: ExpandPromptHandler,
    private readonly onListSkills: ListSkillsHandler = async () => [],
    private readonly onReady: () => Promise<void> = async () => {},
    private readonly onMcpDecision: McpDecisionHandler = async () => {},
    private readonly onListModels: ListModelsHandler = async () => ({ current: null, options: [] }),
    private readonly onSetModel: SetModelHandler = async () => ({ current: null, options: [] }),
    private readonly onSetReasoningEffort: SetReasoningEffortHandler = async () => ({
      current: null,
      options: [],
    }),
    private readonly onOpenSettings: OpenSettingsHandler = () => {},
    private readonly onOpenMemoryPanel: OpenMemoryPanelHandler = () => {},
    private readonly onListWorkspaceFiles: ListWorkspaceFilesHandler = async () => [],
    private readonly onOpenFile: OpenFileHandler = () => {},
    private readonly onOpenGraphPanel: OpenGraphPanelHandler = () => {},
    private readonly onFetchSessionTranscript: FetchSessionTranscriptHandler = async () => null,
    private readonly onGetPlanMode: GetPlanModeHandler = () => false,
    private readonly onSetPlanMode: SetPlanModeHandler = async () => {},
    private readonly onGetStepReview: GetStepReviewHandler = () => true,
    private readonly onRewindPreview: RewindPreviewHandler = async () => {},
    private readonly onRewindConfirm: RewindConfirmHandler = async () => {},
    private readonly onSetOpenAgents: (agentIds: string[]) => void = () => {},
    private readonly onStopAgent: (agentId: string) => Promise<void> = async () => {},
    private readonly onStopAllAgents: () => Promise<void> = async () => {},
    private readonly onSetOpenTeams: (teamIds: string[]) => void = () => {},
    private readonly onDisbandTeam: (teamId: string) => Promise<void> = async () => {},
    private readonly onTeamPlanDecision: (
      threadId: string, gateId: string, decision: "approve" | "feedback" | "reject",
      feedback?: string,
    ) => Promise<void> = async () => {}
  ) {
    super({
      postMessage: (message) => { this.drafts.observe(message); void this.panel?.webview.postMessage(message); },
      isOpen: () => this.panel !== null,
      identity: () => this.panel,
      createRequestId: randomUUID,
      listModels: () => this.onListModels(),
    });
  }

  /** Injects the settings handler factory for the embedded settings overlay. Called
   * once by extension.ts after clientFactory is available; the handler itself is
   * built per webview mount in registerHandlers. */
  setSettingsHandlerFactory(factory: SettingsHandlerFactory): void {
    this.settingsHandlerFactory = factory;
  }

  /** Called by the webview serializer when VS Code restores a persisted panel. */
  reattach(restoredPanel: vscode.WebviewPanel): void {
    this.drafts.resetView();
    this.panel = restoredPanel;
    // Restored panels keep serialized options — reset before building html so
    // the CSP and localResourceRoots are correct for the new build path.
    this.panel.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist"),
      ],
    };
    this.panel.webview.html = this.buildHtml();
    this.themeSubscription?.();
    this.themeSubscription = themeHub.subscribe((message) => {
      void this.panel?.webview.postMessage(message);
    });
    this.registerHandlers();
  }

  show(): void {
    if (this.panel) {
      this.panel.reveal();
      return;
    }
    this.panel = vscode.window.createWebviewPanel(
      "crucibleChat",
      "Crucible Chat",
      vscode.ViewColumn.Two,
      {
        enableScripts: true,
        // Keep the page alive while the tab is hidden. Without it VS Code destroys the
        // webview on every tab switch and the chat remounts on the thread list, losing
        // the open thread, scroll, drafts, expanded agent rows and the agent window.
        retainContextWhenHidden: true,
        localResourceRoots: [
            vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist"),
        ],
      }
    );
    this.panel.webview.html = this.buildHtml();
    this.themeSubscription?.();
    this.themeSubscription = themeHub.subscribe((message) => {
      void this.panel?.webview.postMessage(message);
    });
    this.registerHandlers();
  }

  private registerHandlers(): void {
    if (!this.panel) return;
    // (Re)build the embedded-settings handler bound to this webview's poster. Fresh
    // per mount, so its restartRequired state resets on reload — matching the panel.
    this.settingsHandle = this.settingsHandlerFactory
      ? this.settingsHandlerFactory((out) => {
          void this.panel?.webview.postMessage(out);
        })
      : null;
    const mountedPanel = this.panel;
    const actions: ChatMessageActions = {
      legacyTaskActions: {
        onPlanAction: (...args) => this.onPlanAction(...args),
        onApplyInlineChange: (...args) => this.onApplyInlineChange(...args),
        onDiscardInlineChange: (...args) => this.onDiscardInlineChange(...args),
        onScopeDecision: (...args) => this.onScopeDecision(...args),
        onValidationDecision: (...args) => this.onValidationDecision(...args),
        onStepDecision: (...args) => this.onStepDecision(...args),
        onAcceptTask: (...args) => this.onAcceptTask(...args),
        onRejectTask: (...args) => this.onRejectTask(...args),
        onResumeTask: (...args) => this.onResumeTask(...args),
        onAbortTask: (...args) => this.onAbortTask(...args),
      },
      onViewDiffFile: (...args) => this.onViewDiffFile(...args),
      onCheckChatReceipt: () => this.receiptCheckHandler(),
      onMessage: (...args) => this.drafts.send(args[6], () => this.onMessage(...args)),
      onNewChat: () => this.newChat(),
      onSwitchThread: id => this.switchThread(id),
      onCommandDecision: (...args) => this.onCommandDecision(...args),
      onGateDecision: action => this.gateActions?.submit(action) ?? Promise.resolve(),
      onModeDecision: (...args) => this.onModeDecision(...args),
      onClarifyDecision: (...args) => this.onClarifyDecision(...args),
      onEditDecision: (...args) => this.onEditDecision(...args),
      onStopTurn: (...args) => this.onStopTurn(...args),
      onSetReviewPref: (...args) => this.onSetReviewPref(...args),
      onListPrompts: (...args) => this.onListPrompts(...args),
      onExpandPrompt: (...args) => this.onExpandPrompt(...args),
      onListSkills: (...args) => this.onListSkills(...args),
      onMcpDecision: (...args) => this.onMcpDecision(...args),
      onListModels: (...args) => this.onListModels(...args),
      onSetModel: (...args) => this.onSetModel(...args),
      onSetReasoningEffort: (...args) => this.onSetReasoningEffort(...args),
      onOpenSettings: (...args) => this.onOpenSettings(...args),
      onOpenMemoryPanel: (...args) => this.onOpenMemoryPanel(...args),
      onListWorkspaceFiles: (...args) => this.onListWorkspaceFiles(...args),
      onOpenFile: (...args) => this.onOpenFile(...args),
      onOpenGraphPanel: (...args) => this.onOpenGraphPanel(...args),
      onFetchSessionTranscript: (...args) => this.onFetchSessionTranscript(...args),
      onGetPlanMode: (...args) => this.onGetPlanMode(...args),
      onSetPlanMode: enabled => this.gateActions?.setPlanMode(enabled) ?? this.onSetPlanMode(enabled),
      onGetStepReview: (...args) => this.onGetStepReview(...args),
      onRewindPreview: (...args) => this.onRewindPreview(...args),
      onRewindConfirm: (...args) => this.onRewindConfirm(...args),
      onReady: (...args) => this.onReady(...args),
      onSetOpenAgents: (...args) => this.onSetOpenAgents(...args),
      onStopAgent: (...args) => this.onStopAgent(...args),
      onStopAllAgents: (...args) => this.onStopAllAgents(...args),
      onSetOpenTeams: (...args) => this.onSetOpenTeams(...args),
      onDisbandTeam: (...args) => this.onDisbandTeam(...args),
      onTeamPlanDecision: (...args) => this.onTeamPlanDecision(...args),
    };
    const view: ChatMessageView = {
      postMessage: (message) => { void this.panel?.webview.postMessage(message); },
      replayState: () => this.replayState(),
      answerFullAccessQuestion: (requestId, choice) => this.answerFullAccessQuestion(requestId, choice),
      setApprovalMode: (mode, confirmed, remember) => this.approvalHandler?.(mode, confirmed, remember) ?? Promise.resolve(),
      refreshModels: () => this.refreshModels(),
      showError: (message) => { void vscode.window.showErrorMessage(message); },
    };
    this.panel.webview.onDidReceiveMessage((msg: unknown) => {
      if (mountedPanel !== this.panel) return;
      const themeReply = themeReadyReply(msg, themeHub);
      if (themeReply) {
        void this.panel?.webview.postMessage(themeReply);
        return;
      }
      if (!msg || typeof msg !== "object" || Array.isArray(msg)) return;
      const m = msg as Record<string, unknown>;
      if (m["type"] === "requestFileReferenceCatalog" || m["type"] === "openFileReference") {
        void this.fileReferenceBridge?.handle(msg).catch(() => {
          void vscode.window.showInformationMessage("Unable to refresh workspace file links.");
        });
        return;
      }
      // Settings overlay (embedded SettingsApp) speaks settings/* — route to the
      // dedicated handler, which posts settings/state|error|instructions back.
      if (typeof m["type"] === "string" && (m["type"] as string).startsWith("settings/")) {
        void this.settingsHandle?.(m as unknown as SettingsInMsg);
        return;
      }
      if (m["type"] === "webviewReady") { this.draftGeneration = {}; this.drafts.resetView(); }
      if (this.drafts.handle(m)) return;
      try {
        this.drafts.dispatch(m);
        void routeChatMessage(msg, actions, view);
      } catch (error) {
        if (typeof m["draftId"] === "string") this.drafts.rejectPrepared(m["draftId"]);
        void vscode.window.showErrorMessage(error instanceof Error ? error.message : "Chat request failed.");
      }
    });
    this.panel.onDidDispose(() => {
      if (mountedPanel !== this.panel) return;
      this.disposeViewState();
      this.drafts.resetView();
      this.themeSubscription?.();
      this.themeSubscription = null;
      this.panel = null;
    });
  }

  private buildHtml(): string {
    const distPath = vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist");
    let rawHtml: string;
    try {
      rawHtml = fs.readFileSync(vscode.Uri.joinPath(distPath, "index.html").fsPath, "utf8");
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Chat</title></head>
<body style="background:#1e1e1e;color:#ccc;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;margin:0">
  <p style="font-size:1.1em">Chat webview build is missing.</p>
  <pre style="font-family:monospace;background:#2d2d2d;padding:0.5em 1em;border-radius:4px">Run: npm run -w crucible-vscode-extension build</pre>
  <p style="font-size:0.8em;color:#888">${errMsg}</p>
</body>
</html>`;
    }

    const nonce = Array.from({ length: 16 }, () =>
      Math.floor(Math.random() * 256).toString(16).padStart(2, "0")
    ).join("");
    const cspSource = this.panel!.webview.cspSource;

    // Vite emits relative refs (base "./"): src="./assets/index.js" href="./assets/index.css"
    let html = rawHtml.replace(/(src|href)="\.\/(assets\/[^"]+)"/g, (_m, attr: string, assetPath: string) => {
      const uri = this.panel!.webview.asWebviewUri(vscode.Uri.joinPath(distPath, assetPath));
      return `${attr}="${uri}"`;
    });
    // Lookahead ensures we only match <script> tags (not e.g. <script-runner>) and the
    // space/> following the tag name is preserved by the lookahead (not consumed).
    html = html.replace(/<script(?=[\s>])/g, `<script nonce="${nonce}"`);
    html = html.replace(
      "<head>",
      `<head>\n<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' ${cspSource}; script-src 'nonce-${nonce}' ${cspSource}; img-src ${cspSource} data:; font-src ${cspSource};">`
    );
    return injectThemeAttributes(html, themeHub.settings);
  }
}
