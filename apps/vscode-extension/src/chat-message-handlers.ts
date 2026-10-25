// Shared host callbacks. No VS Code or Electron runtime dependency.
import type { CommandDecision, EffortSupport, McpToolDecision, ReasoningEffort } from "@crucible/editor-client";

export type ChatMessageHandler = (
  message: string,
  stepReview?: boolean,
  forcedSkills?: string[],
  mentionedPaths?: string[],
  planMode?: boolean,
  imageAttachments?: { name: string; mediaType: string; dataUrl: string; bytes: number }[],
  draftId?: string,
  originalDraftText?: string
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
export type CommandDecisionHandler = (taskId: string, decision: CommandDecision, gateId: string) => Promise<void>;
export type StepDecisionHandler = (taskId: string, decision: "accept" | "discard") => Promise<void>;
// Controller gates (Phase F): mode is a streamed dispatch, edit a plain ack.
export type ModeDecisionHandler = (threadId: string, mode: string) => Promise<void>;
export type ClarifyDecisionHandler = (threadId: string, answer: string) => Promise<void>;
export type EditDecisionHandler = (threadId: string, decision: "accept" | "reject", reason: string, gateId: string) => Promise<void>;
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
export type McpDecisionHandler = (threadId: string, decision: McpToolDecision, gateId: string) => Promise<void>;

// Composer model quick-swap (options are ModelOption[] from composer-models.ts, kept
// as unknown[] here since chat-panel doesn't import that type; the webview mirrors it).
export interface ComposerModelState {
  current: { backend: string; model: string } | null;
  /** The ChatGPT plan is in use: show "Using ChatGPT plan · Manage usage". */
  usesChatgptPlan?: boolean;
  /** The active backend uses the Claude subscription adapter. */
  usesClaudePlan?: boolean;
  options: unknown[];
  // Reasoning-effort dial state, carried on the same modelList round-trip since
  // capability (EffortSupport) is per-(backend, model) — it's stale the instant
  // the model swaps, so it has to refresh in lockstep with the model list.
  effort?: {
    level: ReasoningEffort | null;
    support: EffortSupport | null;
    // Sourced from GET /v1/config (ProviderRuntime.reasoning_effort_note), so it
    // rides every modelList refresh — including a plain listModels poll, not just
    // the swap that produced it. This is what makes the clamp explanation durable
    // across a model swap or reload instead of clearing on the next refresh.
    note?: string | null;
  };
}
export type ListModelsHandler = () => Promise<ComposerModelState>;
export type SetModelHandler = (backend: string, model: string) => Promise<ComposerModelState>;
export type SetReasoningEffortHandler = (
  level: ReasoningEffort | null,
) => Promise<ComposerModelState>;
export type OpenSettingsHandler = (section?: string) => void;
export type OpenMemoryPanelHandler = () => void;
export type OpenGraphPanelHandler = () => void;
export type ListWorkspaceFilesHandler = () => Promise<string[]>;
export type FetchSessionTranscriptHandler = (
  sessionId: string,
) => Promise<import("@crucible/editor-client").SessionTranscript | null>;
export type OpenFileHandler = (relativePath: string) => void;
// Sticky Plan Mode toggle (survives across threads/reloads via extension globalState).
export type GetPlanModeHandler = () => boolean;
export type GetStepReviewHandler = () => boolean;
/** Chat rewind: preview feeds the confirm dialog, confirm performs the destructive act. */
export type RewindPreviewHandler = (messageId: string) => Promise<void>;
export type RewindConfirmHandler = (messageId: string) => Promise<void>;
export type SetPlanModeHandler = (enabled: boolean, isCurrent?: () => boolean) => Promise<void>;


export type GateDecisionHandler = (action: import("./gate-actions.js").GateAction) => Promise<void>;
