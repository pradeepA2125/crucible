import * as fs from "node:fs";
import * as vscode from "vscode";
import type { ChatMessage, ChatThreadSummary, CommandDecision, DocWriteDecision, McpToolDecision } from "@crucible/editor-client";
import type { LiveGateView, LivePlanView, LiveSessionsView, LiveTodosView } from "./controller.js";
import type { SettingsInMsg, SettingsOutMsg } from "./settings-data.js";

// Builds a settings message handler bound to a poster into THIS webview — the chat
// bundle now embeds the settings UI (floating overlay), so its settings/* messages
// are handled here and replies posted back into the same webview. See settings-deps.ts.
export type SettingsHandlerFactory = (
  post: (msg: SettingsOutMsg) => void,
) => (msg: SettingsInMsg) => void | Promise<void>;

export type ChatMessageHandler = (
  message: string,
  stepReview?: boolean,
  forcedSkills?: string[],
  mentionedPaths?: string[]
) => Promise<void>;
export type PlanCardActionHandler = (
  taskId: string,
  action: "implement" | "feedback",
  feedback?: string
) => Promise<void>;
export type InlineChangeActionHandler = (taskId: string) => Promise<void>;
export type ViewDiffFileHandler = (relativePath: string, shadowPath: string) => Promise<void>;
export type NewChatHandler = () => Promise<void>;
export type SwitchThreadHandler = (threadId: string) => Promise<void>;
export type ScopeDecisionHandler = (taskId: string, files: string[], decision: "approve" | "reject", remember: boolean) => Promise<void>;
export type ValidationDecisionHandler = (taskId: string, decision: "accept" | "reject") => Promise<void>;
export type CommandDecisionHandler = (taskId: string, decision: CommandDecision) => Promise<void>;
export type StepDecisionHandler = (taskId: string, decision: "accept" | "discard") => Promise<void>;
// Controller gates (Phase F): mode is a streamed dispatch, edit a plain ack.
export type ModeDecisionHandler = (threadId: string, mode: string) => Promise<void>;
export type ClarifyDecisionHandler = (threadId: string, answer: string) => Promise<void>;
export type EditDecisionHandler = (threadId: string, decision: "accept" | "reject", reason: string) => Promise<void>;
export type AcceptTaskHandler = (taskId: string) => Promise<void>;
export type RejectTaskHandler = (taskId: string, reason: string) => Promise<void>;
export type ResumeTaskHandler = (taskId: string, stage: "plan" | "execute") => Promise<void>;
export type StopTurnHandler = () => void;
// Tier B: Stop the running task (work-bar). revert rolls the workspace back vs keeps changes.
export type AbortTaskHandler = (revert: boolean) => Promise<void>;
// Tier B: live-mutable "Review each step" preference for the running task.
export type SetReviewPrefHandler = (autoAccept: boolean) => Promise<void>;
// P1: prompt-file expansion in the composer (.crucible/prompts/<name>.md).
export type ListPromptsHandler = () => Promise<string[]>;
export type ExpandPromptHandler = (
  name: string,
  args: string
) => Promise<{ found: boolean; text: string }>;
// P2: skill catalog for the composer's /skill forced-load.
export type ListSkillsHandler = () => Promise<{ name: string; description: string }[]>;
// P3: controller mcp_tool gate — approve/reject an external MCP tool call.
export type McpDecisionHandler = (threadId: string, decision: McpToolDecision) => Promise<void>;
// Controller doc_write gate — approve/reject a write_doc file write.
export type DocDecisionHandler = (threadId: string, decision: DocWriteDecision) => Promise<void>;

// Composer model quick-swap (options are ModelOption[] from composer-models.ts, kept
// as unknown[] here since chat-panel doesn't import that type; the webview mirrors it).
export interface ComposerModelState {
  current: { backend: string; model: string } | null;
  options: unknown[];
}
export type ListModelsHandler = () => Promise<ComposerModelState>;
export type SetModelHandler = (backend: string, model: string) => Promise<ComposerModelState>;
export type OpenSettingsHandler = (section?: string) => void;
export type OpenMemoryPanelHandler = () => void;
export type OpenGraphPanelHandler = () => void;
export type ListWorkspaceFilesHandler = () => Promise<string[]>;
export type FetchSessionTranscriptHandler = (
  sessionId: string,
) => Promise<import("@crucible/editor-client").SessionTranscript | null>;
export type OpenFileHandler = (relativePath: string) => void;

export class ChatPanel {
  private panel: vscode.WebviewPanel | null = null;
  // Settings handler for the embedded floating settings overlay. The factory is
  // injected by extension.ts (it needs clientFactory, defined after this panel is
  // constructed); the concrete handler is (re)built per webview mount in
  // registerHandlers so it can post replies into the live panel.
  private settingsHandlerFactory: SettingsHandlerFactory | null = null;
  private settingsHandle: ((msg: SettingsInMsg) => void | Promise<void>) | null = null;

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
    private readonly onDocDecision: DocDecisionHandler = async () => {},
    private readonly onListModels: ListModelsHandler = async () => ({ current: null, options: [] }),
    private readonly onSetModel: SetModelHandler = async () => ({ current: null, options: [] }),
    private readonly onOpenSettings: OpenSettingsHandler = () => {},
    private readonly onOpenMemoryPanel: OpenMemoryPanelHandler = () => {},
    private readonly onListWorkspaceFiles: ListWorkspaceFilesHandler = async () => [],
    private readonly onOpenFile: OpenFileHandler = () => {},
    private readonly onOpenGraphPanel: OpenGraphPanelHandler = () => {},
    private readonly onFetchSessionTranscript: FetchSessionTranscriptHandler = async () => null
  ) {}

  /** Injects the settings handler factory for the embedded settings overlay. Called
   * once by extension.ts after clientFactory is available; the handler itself is
   * built per webview mount in registerHandlers. */
  setSettingsHandlerFactory(factory: SettingsHandlerFactory): void {
    this.settingsHandlerFactory = factory;
  }

  /** Called by the webview serializer when VS Code restores a persisted panel. */
  reattach(restoredPanel: vscode.WebviewPanel): void {
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
        localResourceRoots: [
            vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist"),
        ],
      }
    );
    this.panel.webview.html = this.buildHtml();
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
    this.panel.webview.onDidReceiveMessage((msg: unknown) => {
      const m = msg as Record<string, unknown>;
      // Settings overlay (embedded SettingsApp) speaks settings/* — route to the
      // dedicated handler, which posts settings/state|error|instructions back.
      if (typeof m["type"] === "string" && (m["type"] as string).startsWith("settings/")) {
        void this.settingsHandle?.(m as unknown as SettingsInMsg);
        return;
      }
      let p: Promise<void>;
      if (m["type"] === "webviewReady") {
        // Webview state resets on reload; the workbar is SSE-fed (not in the
        // /live poll), so replay the last posted value or step progress shows
        // a bare "Executing…" until the next step_started event.
        if (this.lastWorkbarInfo !== null) {
          this.updateWorkbar(this.lastWorkbarInfo);
        }
        p = this.onReady();
      } else if (m["type"] === "sendMessage") {
        const forcedSkills = Array.isArray(m["forcedSkills"])
          ? (m["forcedSkills"] as string[])
          : undefined;
        const mentionedPaths = Array.isArray(m["mentionedPaths"])
          ? (m["mentionedPaths"] as string[])
          : undefined;
        p = this.onMessage(m["text"] as string, m["stepReview"] === true, forcedSkills, mentionedPaths);
      } else if (m["type"] === "implementPlan") {
        p = this.onPlanAction(m["taskId"] as string, "implement");
      } else if (m["type"] === "planFeedback") {
        p = this.onPlanAction(m["taskId"] as string, "feedback", m["feedback"] as string);
      } else if (m["type"] === "newChat") {
        p = this.onNewChat();
      } else if (m["type"] === "switchThread") {
        p = this.onSwitchThread(m["threadId"] as string);
      } else if (m["type"] === "applyInlineChange") {
        p = this.onApplyInlineChange(m["taskId"] as string);
      } else if (m["type"] === "discardInlineChange") {
        p = this.onDiscardInlineChange(m["taskId"] as string);
      } else if (m["type"] === "viewDiffFile") {
        p = this.onViewDiffFile(m["path"] as string, m["shadowPath"] as string ?? "");
      } else if (m["type"] === "scopeDecision") {
        const files = Array.isArray(m["files"]) ? m["files"] as string[] : [];
        const decision = m["decision"] === "approve" ? "approve" : "reject";
        const remember = m["remember"] === true;
        p = this.onScopeDecision(m["taskId"] as string, files, decision, remember);
      } else if (m["type"] === "validationDecision") {
        const decision = m["decision"] === "accept" ? "accept" : "reject";
        p = this.onValidationDecision(m["taskId"] as string, decision);
      } else if (m["type"] === "commandDecision") {
        const decision: CommandDecision = {
          approve: m["approve"] === true,
          remember: m["remember"] === true,
          scope: (m["scope"] === "prefix" || m["scope"] === "binary") ? m["scope"] : "exact",
          ruleValue: typeof m["ruleValue"] === "string" ? (m["ruleValue"] as string) : undefined,
        };
        p = this.onCommandDecision(m["taskId"] as string, decision);
      } else if (m["type"] === "mcpDecision") {
        p = this.onMcpDecision(m["threadId"] as string, {
          approve: m["approve"] === true,
          remember: m["remember"] === true,
        });
      } else if (m["type"] === "docDecision") {
        p = this.onDocDecision(m["threadId"] as string, {
          approve: m["approve"] === true,
        });
      } else if (m["type"] === "stepDecision") {
        const decision = m["decision"] === "accept" ? "accept" : "discard";
        p = this.onStepDecision(m["taskId"] as string, decision);
      } else if (m["type"] === "modeDecision") {
        p = this.onModeDecision(m["threadId"] as string, m["mode"] as string);
      } else if (m["type"] === "clarifyDecision") {
        p = this.onClarifyDecision(m["threadId"] as string, m["answer"] as string);
      } else if (m["type"] === "editDecision") {
        const decision = m["decision"] === "accept" ? "accept" : "reject";
        p = this.onEditDecision(m["threadId"] as string, decision, (m["reason"] as string) ?? "");
      } else if (m["type"] === "acceptTask") {
        p = this.onAcceptTask(m["taskId"] as string);
      } else if (m["type"] === "rejectTask") {
        p = this.onRejectTask(m["taskId"] as string, (m["reason"] as string) ?? "");
      } else if (m["type"] === "resumeTask") {
        const stage = m["stage"] === "plan" ? "plan" : "execute";
        p = this.onResumeTask(m["taskId"] as string, stage);
      } else if (m["type"] === "stopTurn") {
        this.onStopTurn();
        return;
      } else if (m["type"] === "abortTask") {
        p = this.onAbortTask(m["revert"] === true);
      } else if (m["type"] === "setReviewPref") {
        p = this.onSetReviewPref(m["autoAccept"] === true);
      } else if (m["type"] === "listPrompts") {
        p = (async () => {
          const names = await this.onListPrompts();
          this.panel?.webview.postMessage({ type: "promptList", names });
        })();
      } else if (m["type"] === "listSkills") {
        p = (async () => {
          const skills = await this.onListSkills();
          this.panel?.webview.postMessage({ type: "skillList", skills });
        })();
      } else if (m["type"] === "listWorkspaceFiles") {
        p = (async () => {
          const paths = await this.onListWorkspaceFiles();
          this.panel?.webview.postMessage({ type: "workspaceFileList", paths });
        })();
      } else if (m["type"] === "fetchSessionTranscript") {
        const sessionId = m["sessionId"] as string;
        p = (async () => {
          const transcript = await this.onFetchSessionTranscript(sessionId);
          this.panel?.webview.postMessage({ type: "sessionTranscript", sessionId, transcript });
        })();
      } else if (m["type"] === "openFile") {
        this.onOpenFile(m["path"] as string);
        return;
      } else if (m["type"] === "expandPrompt") {
        const name = m["name"] as string;
        const args = (m["args"] as string) ?? "";
        p = (async () => {
          const result = await this.onExpandPrompt(name, args);
          this.panel?.webview.postMessage({
            type: "promptExpanded",
            name,
            found: result.found,
            text: result.text,
          });
        })();
      } else if (m["type"] === "listModels") {
        p = (async () => {
          const result = await this.onListModels();
          this.panel?.webview.postMessage({ type: "modelList", ...result });
        })();
      } else if (m["type"] === "setModel") {
        // Handles its own errors so a swap failure never re-enables input via
        // the generic p.catch — it renders in-popover and the composer stays put.
        p = (async () => {
          try {
            const result = await this.onSetModel(m["backend"] as string, m["model"] as string);
            this.panel?.webview.postMessage({ type: "modelList", ...result });
          } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.panel?.webview.postMessage({ type: "modelSwapError", message });
          }
        })();
      } else if (m["type"] === "openSettings") {
        this.onOpenSettings(typeof m["section"] === "string" ? m["section"] : undefined);
        return;
      } else if (m["type"] === "openMemoryPanel") {
        this.onOpenMemoryPanel();
        return;
      } else if (m["type"] === "openGraphPanel") {
        this.onOpenGraphPanel();
        return;
      } else {
        return;
      }
      p.catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        this.panel?.webview.postMessage({ type: "setInputEnabled", enabled: true });
        vscode.window.showErrorMessage(`Chat error: ${message}`);
      });
    });
    this.panel.onDidDispose(() => {
      this.panel = null;
    });
  }

  appendMessage(message: ChatMessage): void {
    this.panel?.webview.postMessage({ type: "appendMessage", message });
  }

  appendChunk(chunk: string): void {
    this.panel?.webview.postMessage({ type: "appendChunk", chunk });
  }

  showThinking(message: string): void {
    this.panel?.webview.postMessage({ type: "showThinking", message });
  }

  updateThinking(message: string): void {
    this.panel?.webview.postMessage({ type: "updateThinking", message });
  }

  hideThinking(): void {
    this.panel?.webview.postMessage({ type: "hideThinking" });
  }

  setInputEnabled(enabled: boolean): void {
    this.panel?.webview.postMessage({ type: "setInputEnabled", enabled });
  }

  renderThreadList(
    threads: ChatThreadSummary[],
    activeThreadId: string
  ): void {
    this.panel?.webview.postMessage({ type: "renderThreadList", threads, activeThreadId });
  }

  clearThread(): void {
    this.panel?.webview.postMessage({ type: "clearThread" });
  }

  resolveInlineChangeCard(taskId: string, resolution: "applied" | "discarded"): void {
    this.panel?.webview.postMessage({ type: "resolveInlineChangeCard", taskId, resolution });
  }

  updateThreadTitle(threadId: string, title: string): void {
    this.panel?.webview.postMessage({ type: "thread_title_updated", payload: { thread_id: threadId, title } });
  }

  appendThinkingEntry(text: string): void {
    this.panel?.webview.postMessage({ type: "appendThinkingEntry", text });
  }

  appendThinkingChunk(chunk: string): void {
    this.panel?.webview.postMessage({ type: "appendThinkingChunk", chunk });
  }

  finalizeAgentMessage(): void {
    this.panel?.webview.postMessage({ type: "finalizeAgentMessage" });
  }

  // Live, state-driven cards (Class A). The webview keeps a single slot per kind and
  // replaces in place, so these are safe to call every poll tick.
  renderLiveGate(gate: LiveGateView): void {
    this.panel?.webview.postMessage({ type: "renderLiveGate", gate });
  }

  clearLiveGate(): void {
    this.panel?.webview.postMessage({ type: "clearLiveGate" });
  }

  renderLivePlan(plan: LivePlanView): void {
    this.panel?.webview.postMessage({ type: "renderLivePlan", plan });
  }

  clearLivePlan(): void {
    this.panel?.webview.postMessage({ type: "clearLivePlan" });
  }

  renderLiveReview(review: { taskId: string; modifiedFiles: string[]; shadowWorkspacePath: string | null; stepsCompleted: number | null; stepsTotal: number | null; deviations: string[] }): void {
    this.panel?.webview.postMessage({ type: "renderLiveReview", review });
  }

  clearLiveReview(): void {
    this.panel?.webview.postMessage({ type: "clearLiveReview" });
  }

  renderLiveError(error: { taskId: string; status: "FAILED" | "ABORTED"; detail?: string }): void {
    this.panel?.webview.postMessage({ type: "renderLiveError", error });
  }

  clearLiveError(): void {
    this.panel?.webview.postMessage({ type: "clearLiveError" });
  }

  renderLiveTodos(todos: LiveTodosView): void {
    this.panel?.webview.postMessage({ type: "renderLiveTodos", todos });
  }

  clearLiveTodos(): void {
    this.panel?.webview.postMessage({ type: "clearLiveTodos" });
  }

  renderLiveSessions(sessions: LiveSessionsView): void {
    this.panel?.webview.postMessage({ type: "renderLiveSessions", sessions });
  }

  clearLiveSessions(): void {
    this.panel?.webview.postMessage({ type: "clearLiveSessions" });
  }

  sendLiveStatus(status: string | null, turnActive: boolean): void {
    this.panel?.webview.postMessage({ type: "liveStatus", status, turnActive });
  }

  appendToolEvent(event: { id: number; tool: string; args: Record<string, unknown>; thought?: string; source: "explore" | "execution" | "planning" }): void {
    this.panel?.webview.postMessage({ type: "appendToolEvent", event });
  }

  appendToolResult(id: number, output: string, isError: boolean): void {
    this.panel?.webview.postMessage({ type: "appendToolResult", id, output, isError });
  }

  private lastWorkbarInfo: { stepIndex?: number; totalSteps?: number; stepTitle?: string; phaseLabel?: string } | null = null;

  updateWorkbar(info: { stepIndex?: number; totalSteps?: number; stepTitle?: string; phaseLabel?: string } | null): void {
    this.lastWorkbarInfo = info;
    this.panel?.webview.postMessage({ type: "updateWorkbar", info });
  }

  updateRetryStatus(status: { attempt: number; max_attempts: number; reason: string; message: string } | null): void {
    this.panel?.webview.postMessage({ type: "updateRetryStatus", status });
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
    return html;
  }
}
