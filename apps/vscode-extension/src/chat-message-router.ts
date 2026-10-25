// Shared dispatch for the existing chat protocol. Desktop must validate its IPC
// envelope separately; this module does not grant access to a project or backend.
import { GateActionSchema } from "./gate-actions.js";
import type { CommandDecision, ReasoningEffort } from "@crucible/editor-client";
import type {
  AbortTaskHandler,
  AcceptTaskHandler,
  ChatMessageHandler,
  ClarifyDecisionHandler,
  CommandDecisionHandler,
  EditDecisionHandler,
  ExpandPromptHandler,
  FetchSessionTranscriptHandler,
  GetPlanModeHandler,
  GetStepReviewHandler,
  InlineChangeActionHandler,
  ListModelsHandler,
  ListPromptsHandler,
  ListSkillsHandler,
  ListWorkspaceFilesHandler,
  McpDecisionHandler,
  ModeDecisionHandler,
  NewChatHandler,
  OpenFileHandler,
  OpenGraphPanelHandler,
  OpenMemoryPanelHandler,
  OpenSettingsHandler,
  PlanCardActionHandler,
  RejectTaskHandler,
  ResumeTaskHandler,
  RewindConfirmHandler,
  RewindPreviewHandler,
  ScopeDecisionHandler,
  SetModelHandler,
  SetPlanModeHandler,
  SetReasoningEffortHandler,
  SetReviewPrefHandler,
  StepDecisionHandler,
  StopTurnHandler,
  SwitchThreadHandler,
  ValidationDecisionHandler,
  ViewDiffFileHandler
} from "./chat-message-handlers.js";

export interface LegacyTaskActions {
  onPlanAction: PlanCardActionHandler;
  onApplyInlineChange: InlineChangeActionHandler;
  onDiscardInlineChange: InlineChangeActionHandler;
  onScopeDecision: ScopeDecisionHandler;
  onValidationDecision: ValidationDecisionHandler;
  onStepDecision: StepDecisionHandler;
  onAcceptTask: AcceptTaskHandler;
  onRejectTask: RejectTaskHandler;
  onResumeTask: ResumeTaskHandler;
  onAbortTask: AbortTaskHandler;
}

export interface ChatMessageActions {
  onGateDecision?: import("./chat-message-handlers.js").GateDecisionHandler;
  onCheckChatReceipt?: () => Promise<void>;
  legacyTaskActions?: LegacyTaskActions;
  onViewDiffFile: ViewDiffFileHandler;
  onMessage: ChatMessageHandler;
  onNewChat: NewChatHandler;
  onSwitchThread: SwitchThreadHandler;
  onCommandDecision: CommandDecisionHandler;
  onModeDecision: ModeDecisionHandler;
  onClarifyDecision: ClarifyDecisionHandler;
  onEditDecision: EditDecisionHandler;
  onStopTurn: StopTurnHandler;
  onSetReviewPref: SetReviewPrefHandler;
  onListPrompts: ListPromptsHandler;
  onExpandPrompt: ExpandPromptHandler;
  onListSkills: ListSkillsHandler;
  onMcpDecision: McpDecisionHandler;
  onListModels: ListModelsHandler;
  onSetModel: SetModelHandler;
  onSetReasoningEffort: SetReasoningEffortHandler;
  onOpenSettings: OpenSettingsHandler;
  onOpenMemoryPanel: OpenMemoryPanelHandler;
  onListWorkspaceFiles: ListWorkspaceFilesHandler;
  onOpenFile: OpenFileHandler;
  onOpenGraphPanel: OpenGraphPanelHandler;
  onFetchSessionTranscript: FetchSessionTranscriptHandler;
  onGetPlanMode: GetPlanModeHandler;
  onSetPlanMode: SetPlanModeHandler;
  onGetStepReview: GetStepReviewHandler;
  onRewindPreview: RewindPreviewHandler;
  onRewindConfirm: RewindConfirmHandler;
  onReady: () => Promise<void>;
  onSetOpenAgents: (agentIds: string[]) => void;
  onStopAgent: (agentId: string) => Promise<void>;
  onStopAllAgents: () => Promise<void>;
  onSetOpenTeams: (teamIds: string[]) => void;
  onDisbandTeam: (teamId: string) => Promise<void>;
  onTeamPlanDecision: (threadId: string, gateId: string, decision: "approve" | "feedback" | "reject", feedback?: string) => Promise<void>;
}
export interface ChatMessageView {
  postMessage(message: Record<string, unknown>): void;
  replayState(): void;
  answerFullAccessQuestion(requestId: unknown, choice: unknown): void;
  setApprovalMode(mode: unknown, confirmed: boolean, remember: boolean): Promise<void>;
  refreshModels(): Promise<void>;
  showError(message: string): void;
}

/** Handle already-routed chat intents; theme/settings/file links belong to host adapters. */
export function routeChatMessage(
  message: unknown, actions: ChatMessageActions, view: ChatMessageView,
): Promise<void> | undefined {
  if (!message || typeof message !== "object" || Array.isArray(message)) return;
  const m = message as Record<string, unknown>;
  const legacy = actions.legacyTaskActions;
  let p: Promise<void>;
  if (m["type"] === "webviewReady") {
    view.replayState();
    void view.postMessage({
      type: "planModeState", enabled: actions.onGetPlanMode ? actions.onGetPlanMode() : false,
    });
    void view.postMessage({
      type: "reviewPrefState",
      enabled: actions.onGetStepReview ? actions.onGetStepReview() : true,
    });
    p = actions.onReady();
  } else if (m["type"] === "fullAccessFirstSendChoice") {
    view.answerFullAccessQuestion(m["requestId"], m["choice"]);
    p = Promise.resolve();
  } else if (m["type"] === "setApprovalMode") {
    p = view.setApprovalMode?.(m["mode"], m["confirmedFullAccess"] === true, m["dontShowAgain"] === true) ?? Promise.resolve();
  } else if (m["type"] === "setPlanMode") {
    // The checkbox is a fully controlled prop (sourced from globalState, not local
    // state, so it survives across threads/reloads) — without echoing the persisted
    // value back here, it would never visually update after a click, and the stale
    // prop value would keep riding every sendMessage until the next webview reload.
    const enabled = m["enabled"] === true;
    p = Promise.resolve(actions.onSetPlanMode ? actions.onSetPlanMode(enabled) : undefined).then(
      () => {
        void view.postMessage({ type: "planModeState", enabled });
      }
    );
  } else if (m["type"] === "checkChatReceipt") {
    p = actions.onCheckChatReceipt?.() ?? Promise.resolve();
  } else if (m["type"] === "sendMessage") {
    const forcedSkills = Array.isArray(m["forcedSkills"])
      ? (m["forcedSkills"] as string[])
      : undefined;
    const mentionedPaths = Array.isArray(m["mentionedPaths"])
      ? (m["mentionedPaths"] as string[])
      : undefined;
    const imageAttachments = Array.isArray(m["imageAttachments"])
      ? (m["imageAttachments"] as { name: string; mediaType: string; dataUrl: string; bytes: number }[])
          .filter((a) => typeof a?.name === "string" && typeof a?.mediaType === "string"
            && typeof a?.dataUrl === "string" && typeof a?.bytes === "number")
      : undefined;
    // ModeGate's "Chat about this approach" feedback field posts sendMessage with
    // no planMode at all (it doesn't track the composer's toggle state) — falling
    // back to `undefined === true` -> false would silently drop a Plan-Mode turn
    // into ACTIVE the moment the user types feedback on the card, contradicting the
    // whole point of Plan Mode persisting through a plan-refinement round-trip.
    // Fall back to the persisted (single-source-of-truth) value only when the
    // field is genuinely absent, not when it's explicitly false.
    const planMode = typeof m["planMode"] === "boolean"
      ? (m["planMode"] as boolean)
      : (actions.onGetPlanMode ? actions.onGetPlanMode() : false);
    p = actions.onMessage(m["text"] as string, m["stepReview"] === true, forcedSkills, mentionedPaths, planMode, imageAttachments, typeof m["draftId"] === "string" ? m["draftId"] : undefined, typeof m["originalDraftText"] === "string" ? m["originalDraftText"] : undefined);
  } else if (m["type"] === "implementPlan") {
    if (!legacy) return;
    p = legacy.onPlanAction(m["taskId"] as string, "implement");
  } else if (m["type"] === "planFeedback") {
    if (!legacy) return;
    p = legacy.onPlanAction(m["taskId"] as string, "feedback", m["feedback"] as string);
  } else if (m["type"] === "newChat") {
    p = actions.onNewChat();
  } else if (m["type"] === "switchThread") {
    p = actions.onSwitchThread(m["threadId"] as string);
  } else if (m["type"] === "applyInlineChange") {
    if (!legacy) return;
    p = legacy.onApplyInlineChange(m["taskId"] as string);
  } else if (m["type"] === "discardInlineChange") {
    if (!legacy) return;
    p = legacy.onDiscardInlineChange(m["taskId"] as string);
  } else if (m["type"] === "viewDiffFile") {
    p = actions.onViewDiffFile(m["path"] as string, m["shadowPath"] as string ?? "");
  } else if (m["type"] === "scopeDecision") {
    if (!legacy) return;
    const files = Array.isArray(m["files"]) ? m["files"] as string[] : [];
    const decision = m["decision"] === "approve" ? "approve" : "reject";
    const remember = m["remember"] === true;
    p = legacy.onScopeDecision(m["taskId"] as string, files, decision, remember);
  } else if (m["type"] === "validationDecision") {
    if (!legacy) return;
    const decision = m["decision"] === "accept" ? "accept" : "reject";
    p = legacy.onValidationDecision(m["taskId"] as string, decision);
  } else if (m["type"] === "commandDecision") {
    const decision: CommandDecision = {
      approve: m["approve"] === true,
      remember: m["remember"] === true,
      scope: (m["scope"] === "prefix" || m["scope"] === "binary") ? m["scope"] : "exact",
      ruleValue: typeof m["ruleValue"] === "string" ? (m["ruleValue"] as string) : undefined,
    };
    p = actions.onCommandDecision(m["taskId"] as string, decision, typeof m["gateId"] === "string" ? m["gateId"] : "");
  } else if (m["type"] === "mcpDecision") {
    p = actions.onMcpDecision(m["threadId"] as string, {
      approve: m["approve"] === true,
      remember: m["remember"] === true,
    }, typeof m["gateId"] === "string" ? m["gateId"] : "");
  } else if (m["type"] === "stepDecision") {
    if (!legacy) return;
    const decision = m["decision"] === "accept" ? "accept" : "discard";
    p = legacy.onStepDecision(m["taskId"] as string, decision);
  } else if (m["type"] === "rewindPreview") {
    p = actions.onRewindPreview(m["messageId"] as string);
  } else if (m["type"] === "rewindConfirm") {
    p = actions.onRewindConfirm(m["messageId"] as string);
  } else if (m["type"] === "gateDecision") {
    const action = GateActionSchema.safeParse(m);
    if (!action.success || !actions.onGateDecision) {
      const origin = GateActionSchema.pick({ threadId: true, gateId: true, kind: true }).safeParse({ threadId: m.threadId, gateId: m.gateId, kind: m.kind });
      if (origin.success) {
        view.postMessage({ type: "decisionRetry", threadId: origin.data.threadId, gateId: origin.data.gateId,
          kind: origin.data.kind === "feedback" ? "mode" : origin.data.kind });
        view.postMessage({ type: "gateDecisionNotice", threadId: origin.data.threadId, gateId: origin.data.gateId,
          message: "This decision could not be submitted. Keep feedback within 65,536 characters and retry the current gate." });
      }
      return;
    }
    p = actions.onGateDecision(action.data).catch(error => {
      view.postMessage({ type: "decisionRetry", threadId: action.data.threadId, gateId: action.data.gateId,
        kind: action.data.kind === "feedback" ? "mode" : action.data.kind });
      view.postMessage({ type: "gateDecisionNotice", threadId: action.data.threadId, gateId: action.data.gateId,
        message: error instanceof Error ? error.message : "Decision could not be sent." });
    });
  } else if (m["type"] === "modeDecision") {
    p = actions.onModeDecision(m["threadId"] as string, m["mode"] as string);
  } else if (m["type"] === "clarifyDecision") {
    p = actions.onClarifyDecision(m["threadId"] as string, m["answer"] as string);
  } else if (m["type"] === "editDecision") {
    const decision = m["decision"] === "accept" ? "accept" : "reject";
    p = actions.onEditDecision(m["threadId"] as string, decision, (m["reason"] as string) ?? "", typeof m["gateId"] === "string" ? m["gateId"] : "");
  } else if (m["type"] === "acceptTask") {
    if (!legacy) return;
    p = legacy.onAcceptTask(m["taskId"] as string);
  } else if (m["type"] === "rejectTask") {
    if (!legacy) return;
    p = legacy.onRejectTask(m["taskId"] as string, (m["reason"] as string) ?? "");
  } else if (m["type"] === "resumeTask") {
    if (!legacy) return;
    const stage = m["stage"] === "plan" ? "plan" : "execute";
    p = legacy.onResumeTask(m["taskId"] as string, stage);
  } else if (m["type"] === "setOpenAgents") {
    const ids = Array.isArray(m["agentIds"])
      ? (m["agentIds"] as unknown[]).filter((x): x is string => typeof x === "string")
      : [];
    actions.onSetOpenAgents(ids);
    return;
  } else if (m["type"] === "stopAgent") {
    p = actions.onStopAgent(String(m["agentId"] ?? ""));
  } else if (m["type"] === "stopAllAgents") {
    p = actions.onStopAllAgents();
  } else if (m["type"] === "setOpenTeams") {
    const ids = Array.isArray(m["teamIds"])
      ? (m["teamIds"] as unknown[]).filter((x): x is string => typeof x === "string")
      : [];
    actions.onSetOpenTeams(ids);
    return;
  } else if (m["type"] === "disbandTeam") {
    p = actions.onDisbandTeam(String(m["teamId"] ?? ""));
  } else if (m["type"] === "teamPlanDecision") {
    const d = m["decision"];
    if (d !== "approve" && d !== "feedback" && d !== "reject") return;
    p = actions.onTeamPlanDecision(String(m["threadId"] ?? ""), String(m["gateId"] ?? ""), d,
                                typeof m["feedback"] === "string" ? m["feedback"] : undefined);
  } else if (m["type"] === "stopTurn") {
    actions.onStopTurn();
    return;
  } else if (m["type"] === "abortTask") {
    if (!legacy) return;
    p = legacy.onAbortTask(m["revert"] === true);
  } else if (m["type"] === "setReviewPref") {
    // autoAccept is the INVERSE of the checkbox: checked = review each step =
    // do not auto-accept. Persist, then echo the checkbox value back — the box is
    // a controlled prop, so without the echo it never visually updates.
    const autoAccept = m["autoAccept"] === true;
    p = Promise.resolve(actions.onSetReviewPref(autoAccept)).then(() => {
      void view.postMessage({
        type: "reviewPrefState", enabled: !autoAccept,
      });
    });
  } else if (m["type"] === "listPrompts") {
    p = (async () => {
      const names = await actions.onListPrompts();
      view.postMessage({ type: "promptList", names });
    })();
  } else if (m["type"] === "listSkills") {
    p = (async () => {
      const skills = await actions.onListSkills();
      view.postMessage({ type: "skillList", skills });
    })();
  } else if (m["type"] === "listWorkspaceFiles") {
    p = (async () => {
      const paths = await actions.onListWorkspaceFiles();
      view.postMessage({ type: "workspaceFileList", paths });
    })();
  } else if (m["type"] === "fetchSessionTranscript") {
    const sessionId = m["sessionId"] as string;
    p = (async () => {
      const transcript = await actions.onFetchSessionTranscript(sessionId);
      view.postMessage({ type: "sessionTranscript", sessionId, transcript });
    })();
  } else if (m["type"] === "openFile") {
    actions.onOpenFile(m["path"] as string);
    return;
  } else if (m["type"] === "expandPrompt") {
    const name = m["name"] as string;
    const args = (m["args"] as string) ?? "";
    p = (async () => {
      const result = await actions.onExpandPrompt(name, args);
      view.postMessage({
        type: "promptExpanded",
        requestId: m["requestId"],
        name,
        found: result.found,
        text: result.text,
      });
    })();
  } else if (m["type"] === "listModels") {
    p = view.refreshModels();
  } else if (m["type"] === "setModel") {
    // Handles its own errors so a swap failure never re-enables input via
    // the generic p.catch — it renders in-popover and the composer stays put.
    p = (async () => {
      try {
        const result = await actions.onSetModel(m["backend"] as string, m["model"] as string);
        view.postMessage({ type: "modelList", ...result });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        view.postMessage({ type: "modelSwapError", message });
      }
    })();
  } else if (m["type"] === "setReasoningEffort") {
    // Handles its own errors so a swap failure never re-enables input via
    // the generic p.catch — mirrors the setModel branch above.
    p = (async () => {
      try {
        const result = await actions.onSetReasoningEffort(m["level"] as ReasoningEffort | null);
        view.postMessage({ type: "modelList", ...result });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        view.postMessage({ type: "effortSwapError", message });
      }
    })();
  } else if (m["type"] === "openSettings") {
    actions.onOpenSettings(typeof m["section"] === "string" ? m["section"] : undefined);
    return;
  } else if (m["type"] === "openMemoryPanel") {
    actions.onOpenMemoryPanel();
    return;
  } else if (m["type"] === "openGraphPanel") {
    actions.onOpenGraphPanel();
    return;
  } else {
    return;
  }
  return p.catch((err: unknown) => {
    const message = err instanceof Error ? err.message : String(err);
    view.postMessage({ type: "setInputEnabled", enabled: true });
    view.showError(`Chat error: ${message}`);
  });
}
