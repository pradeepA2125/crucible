/** Mirror of src/composer-models.ts ModelOption (webview never imports src/). */
export interface ModelOption {
  backend: string;
  label: string;
  model: string;
  active: boolean;
}

// ── Wire shape of a persisted chat message (mirrors editor-client ChatMessageSchema).
// EVERY message has role + type; cards are discriminated by `type`, not by `role`.
export interface ChatMsg {
  role: "user" | "agent";
  content: string;
  /** Stable rewind anchor. Absent/null on messages persisted before rewind shipped. */
  id?: string | null;
  type: "text" | "plan_card" | "diff_card" | "diff_summary" | "task_card"
      | "scope_card" | "validation_card" | "command_card" | "agent_dispatch"
      | "notice" | "agent_message" | "team_created";
  taskId?: string | null;
  timestamp: string;
  metadata: Record<string, unknown>;
  /** Internal client-side annotation (NOT on the wire): plan-card version signature used for dedup. */
  _sig?: string;
}

// Diff entries arrive snake_case (SSE + /live payloads are not case-mapped).
export interface DiffEntry {
  path: string;
  additions: number;
  deletions: number;
  temp_path?: string;
  // Capped unified diff text (snake_case — SSE/live payloads and persisted
  // metadata are not case-mapped). Absent on pre-v2 messages → FileRow fallback.
  unified_diff?: string;
}

export interface Diagnostic {
  /* backend sends arbitrary level strings (pydantic str) — do not narrow to a literal union */
  level: string;
  message: string;
  source?: string;
}

export interface ThreadSummary {
  threadId: string;
  title: string;
  createdAt: string;
  updatedAt?: string;
  messageCount?: number;
  status?: "running" | "review" | "done" | "failed" | null;
  // Queued, running or gate-parked agents (spec §6), shown next to the chip.
  agentsRunning?: number;
}

// ── Structured tool events ────────────────────────────────────────────────────
export interface ToolEventView {
  id: number;                 // monotonically increasing per turn (extension-assigned)
  tool: string;
  args: Record<string, unknown>;
  thought?: string;
  source: "explore" | "execution" | "planning";
  output?: string;            // filled by the matching toolResult
  isError?: boolean;
  done: boolean;
}

// ── Live slot views ──────────────────────────────────────────────────────────
export interface GateAgentView { id: string; label: string; name: string }

export interface LiveGateView {
  gateId: string;
  kind: "command" | "scope" | "validation" | "step" | "mode" | "edit" | "clarify" | "mcp_tool" | "doc_write";
  taskId: string;
  payload: Record<string, unknown>;  // pending_* payload, snake_case
  agent?: GateAgentView | null;      // the sub-agent that raised it; absent/null = main
}

export interface LivePlanView { taskId: string; planMarkdown: string }

export interface TodoItem {
  title: string;
  status: "pending" | "in_progress" | "done" | "blocked" | "cancelled";
  note: string;
}
export interface LiveTodosView { items: TodoItem[] }

// One live exec session row (mirror of editor-client SessionSummary — this bundle
// does not import it). started_at (epoch sec) is deliberately the only time field:
// the displayed age is computed locally so /live rows stay signature-stable.
export interface LiveSessionItem {
  id: string;
  command: string;
  status: "running" | "exited";
  exit_code: number | null;
  started_at: number;
}
export interface LiveSessionsView { items: LiveSessionItem[] }

// The expandable PTY inspect payload (mirror of editor-client SessionTranscript).
export interface SessionTranscriptView {
  output_tail: string;
  stdin_history: { ts: number; chars: string }[];
  status: "running" | "exited";
  exit_code: number | null;
}

// LLM-authored run narrative (headline + points), shown on the Review/Error cards.
export interface TaskNarrativeView {
  headline: string;
  points: string[];
}

export interface LiveReviewView {
  taskId: string;
  modifiedFiles: string[];
  shadowWorkspacePath: string | null;
  // run summary: derived from result.plan + extension-observed events
  stepsCompleted: number | null;
  stepsTotal: number | null;
  deviations: string[];
  narrative?: TaskNarrativeView;
}

export interface LiveErrorView {
  taskId: string;
  status: "FAILED" | "ABORTED";
  detail?: string;
  narrative?: TaskNarrativeView;
}

export interface WorkbarInfo {
  stepIndex?: number;       // tier 1 — step progress
  totalSteps?: number;
  stepTitle?: string;
  phaseLabel?: string;      // tier 2 — transient event override
}

/** Live token counts during a model call. `thinking` climbs during reasoning,
 * then `output` climbs — the transition that otherwise reads as a hang. */
export interface TokenProgressView {
  thinking: number;
  output: number;
  /** Prompt size, reported before the first delta. Prefill generates nothing, so
   * without this the counter reads zero for most of a large call's wall time and a
   * slow turn looks identical to a wedged one. Null when the provider is unknown. */
  input: number | null;
  /** False while the counts are chars/4 estimates; true once the provider's own
   * usage figure lands on the closing tick. */
  exact: boolean;
}

/** What a rewind would cost — drives the confirm dialog. Mirrors editor-client's
 * RewindPreviewSchema. `blockedByTask` non-null means the POST will 409. */
export interface RewindPreviewView {
  messages: number;
  files: number;
  commandsRun: number;
  blockedByTask: string | null;
  // Agents still running refuse the rewind (spec §8.10).
  blockedByAgents?: string[];
  sessions: { id: string; command: string }[];
}

export interface RetryStatusView {
  attempt: number;
  max_attempts: number;
  reason: string;
  message: string;
}

// ── Sub-agents (mirrors editor-client AgentSummary/AgentDetail — camelCase) ─────
export interface AgentSummaryView {
  agentId: string;
  turnId?: string;
  parentAgentId: string | null;
  depth: number;
  name: string;
  label: string;
  status: string;
  now: string;
  toolCount: number;
  filesChangedCount: number;
  startedAt: string | null;
  endedAt: string | null;
  reportPreview: string;
  // Change only at activation boundaries (spec §6).
  activationCount?: number;
  dispatcherId?: string | null;
  onFinish?: string | null;
  activationStartedAt?: string | null;
  activationEndedAt?: string | null;
}

export interface AgentDetailView extends AgentSummaryView {
  prompt: string;
  report: string;
  filesChanged: string[];
  staleRefusals: number;
  transcript: ChatMsg[];
  lastSeq: number;
}

/** A child-channel event as forwarded by the host (payload keys stay snake_case). */
export interface AgentEventView {
  type: string;
  payload: Record<string, unknown>;
  seq?: number;
}

/** An open agent: its backfilled transcript plus live pills not yet sealed into it. */
export interface AgentViewState {
  detail: AgentDetailView;
  messages: ChatMsg[];
  live: ToolEventView[];
  callIds: Record<number, number>;  // call_index → live pill id
  nextId: number;
}

// ── Agent teams (mirrors editor-client TeamPost/TeamSummary/TeamLive — camelCase) ─────
export interface TeamPostView {
  teamId: string;
  seq: number;
  author: string;
  kind: string;
  recipient: string | null;
  text: string;
  mentions: string[];
  refId: string | null;
  round: number | null;
  payload: Record<string, unknown>;
  closed: string | null;
  createdAt: string;
}

export interface TeamMemberView { label: string; agentId: string; status: string }

export interface TeamSummaryView {
  teamId: string;
  name: string;
  goal: string;
  phase: string;
  round: number;
  maxRounds: number;
  pausedReason: string | null;
  members: TeamMemberView[];
  openProposals: { id: string; author: string; text: string; stances: Record<string, string> }[];
  usage: { requests: number; budget: number };
  createdAt: string;
}

export interface TeamLiveView {
  teamId: string;
  name: string;
  phase: string;
  round: number;
  maxRounds: number;
  pausedReason: string | null;
  members: TeamMemberView[];
}

export interface TeamDetailView extends TeamSummaryView {
  posts: TeamPostView[];
  lastSeq: number;
}

/** A team-channel event as the host forwards it (already camelCase, Task 9). */
export type TeamEventView =
  | { type: "team_post"; post: TeamPostView }
  | { type: "team_phase"; phase: string; round: number; pausedReason: string | null };

/** An open team's board. */
export interface TeamViewState {
  posts: TeamPostView[];
  lastSeq: number;
}

// ── Extension → Webview ──────────────────────────────────────────────────────
export type ExtensionMessage =
  | { type: "appendMessage"; message: ChatMsg }
  | { type: "appendChunk"; chunk: string }
  | { type: "appendThinkingEntry"; text: string }
  | { type: "appendThinkingChunk"; chunk: string }
  | { type: "appendToolEvent"; event: Omit<ToolEventView, "output" | "isError" | "done"> }
  | { type: "appendToolResult"; id: number; output: string; isError: boolean }
  | { type: "updateWorkbar"; info: WorkbarInfo | null }
  | { type: "updateRetryStatus"; status: RetryStatusView | null }
  | { type: "updateTokenProgress"; progress: TokenProgressView | null }
  | { type: "updateEditFailure"; failure: { reason: string; ops: number } | null }
  | { type: "finalizeAgentMessage" }
  | { type: "showThinking"; message: string }
  | { type: "updateThinking"; message: string }
  | { type: "hideThinking" }
  | { type: "setInputEnabled"; enabled: boolean }
  | { type: "renderThreadList"; threads: ThreadSummary[]; activeThreadId: string }
  | { type: "clearThread" }
  | { type: "renderLiveGates"; gates: LiveGateView[] }
  | { type: "renderLivePlan"; plan: LivePlanView }
  | { type: "clearLivePlan" }
  | { type: "renderLiveReview"; review: LiveReviewView }
  | { type: "clearLiveReview" }
  | { type: "renderLiveError"; error: LiveErrorView }
  | { type: "clearLiveError" }
  | { type: "renderLiveTodos"; todos: LiveTodosView }
  | { type: "clearLiveTodos" }
  | { type: "renderLiveSessions"; sessions: LiveSessionsView }
  | { type: "clearLiveSessions" }
  | { type: "sessionTranscript"; sessionId: string; transcript: SessionTranscriptView | null }
  | { type: "liveStatus"; status: string | null; turnActive?: boolean;
      turnKind?: "user" | "notice" | null; agentsRunning?: number }
  // Transcript reconcile (spec §6) and the failed-send rollback (spec §5.3).
  | { type: "replaceMessages"; messages: ChatMsg[] }
  | { type: "removeChatMessage"; id: string }
  | { type: "resolveInlineChangeCard"; taskId: string; resolution: "applied" | "discarded" }
  | { type: "thread_title_updated"; payload: { thread_id: string; title: string } }
  // P1: prompt-file expansion replies from the host
  | { type: "promptList"; names: string[] }
  | { type: "promptExpanded"; name: string; found: boolean; text: string }
  | { type: "workspaceFileList"; paths: string[] }
  // Sticky Plan Mode toggle hydration (extension globalState), pushed on webviewReady
  // and whenever the composer/ModeGate posts setPlanMode back to the host.
  | { type: "planModeState"; enabled: boolean }
  | { type: "reviewPrefState"; enabled: boolean }
  // Chat rewind: the preview opens the confirm dialog; the prefill lands the rewound
  // message's text back in the composer after the server-authoritative reload.
  | { type: "rewindPreviewResult"; preview: RewindPreviewView }
  | { type: "renderAgents"; agents: AgentSummaryView[] }
  | { type: "agentDetail"; agentId: string; detail: AgentDetailView }
  | { type: "agentEvent"; agentId: string; event: AgentEventView }
  | { type: "renderTeams"; teams: TeamSummaryView[] }
  | { type: "renderLiveTeams"; teams: TeamLiveView[] }
  | { type: "teamDetail"; teamId: string; detail: TeamDetailView }
  | { type: "teamEvent"; teamId: string; event: TeamEventView }
  | { type: "composerPrefill"; text: string };

// ── Webview → Extension ──────────────────────────────────────────────────────
export type WebviewMessage =
  | { type: "webviewReady" }
  | { type: "sendMessage"; text: string; stepReview?: boolean; forcedSkills?: string[]; mentionedPaths?: string[]; planMode?: boolean }
  | { type: "rewindPreview"; messageId: string }
  | { type: "rewindConfirm"; messageId: string }
  | { type: "implementPlan"; taskId: string }
  | { type: "planFeedback"; taskId: string; feedback: string }
  | { type: "newChat" }
  | { type: "switchThread"; threadId: string }
  | { type: "applyInlineChange"; taskId: string }
  | { type: "discardInlineChange"; taskId: string }
  | { type: "viewDiffFile"; path: string; shadowPath: string }
  | { type: "scopeDecision"; taskId: string; files: string[]; decision: "approve" | "reject"; remember: boolean }
  | { type: "validationDecision"; taskId: string; decision: "accept" | "reject" }
  | { type: "commandDecision"; taskId: string; gateId: string; approve: boolean; remember?: boolean; scope?: string; ruleValue?: string }
  // Controller mcp_tool gate: approve/reject an external MCP tool call (threadId — no task)
  | { type: "mcpDecision"; threadId: string; gateId: string; approve: boolean; remember: boolean }
  // Controller doc_write gate: approve/reject a write_doc file write (threadId — no task)
  | { type: "docDecision"; threadId: string; approve: boolean }
  | { type: "stepDecision"; taskId: string; decision: "accept" | "discard" }
  // Agentic chat controller: mode-recommendation gate pick + per-edit review decision
  | { type: "modeDecision"; threadId: string; mode: string }
  // Picking "implement" on the mode gate exits Plan Mode — flips the composer's
  // sticky toggle off via the same single write path the toggle itself uses.
  | { type: "setPlanMode"; enabled: boolean }
  | { type: "clarifyDecision"; threadId: string; answer: string }
  | { type: "editDecision"; threadId: string; gateId: string; decision: "accept" | "reject"; reason: string }
  | { type: "acceptTask"; taskId: string }
  | { type: "rejectTask"; taskId: string; reason: string }
  | { type: "resumeTask"; taskId: string; stage: "plan" | "execute" }
  | { type: "stopTurn" }
  // Tier B: cooperative Stop for a running task (revert rolls back vs keeps changes)
  | { type: "abortTask"; revert: boolean }
  // Tier B: live-mutable "Review each step" preference for the running task
  | { type: "setReviewPref"; autoAccept: boolean }
  // P1: prompt-file (.crucible/prompts/<name>.md) listing + expand-before-send
  | { type: "listPrompts" }
  | { type: "expandPrompt"; name: string; args: string }
  // P2: skill (.crucible/skills/<name>/SKILL.md) catalog for /skill forced-load
  | { type: "listSkills" }
  // Composer model quick-swap (ModelMenu) + settings shortcut.
  | { type: "listModels" }
  | { type: "setModel"; backend: string; model: string }
  // Composer reasoning-effort quick-swap (EffortMenu) — rides the same modelList
  // round-trip as setModel since capability is per-(backend, model).
  | { type: "setReasoningEffort"; level: "off" | "low" | "medium" | "high" | "max" }
  // section (optional) deep-links the Settings pane to a section (from the chat drawer).
  | { type: "openSettings"; section?: string }
  // Chat-window shortcut to the standalone Memory Inspector panel/command.
  | { type: "openMemoryPanel" }
  | { type: "openGraphPanel" }
  // Exec sessions: PTY inspect fetch for an expanded session-strip row.
  | { type: "fetchSessionTranscript"; sessionId: string }
  // @-mention composer: workspace file listing + click-to-open.
  | { type: "setOpenAgents"; agentIds: string[] }
  | { type: "setOpenTeams"; teamIds: string[] }
  | { type: "disbandTeam"; teamId: string }
  | { type: "stopAgent"; agentId: string }
  | { type: "stopAllAgents" }
  | { type: "listWorkspaceFiles" }
  | { type: "openFile"; path: string };

// ── App state ─────────────────────────────────────────────────────────────────
export interface StreamingBubble {
  text: string;
  thinkingEntries: string[];
  activeThinkingChunk: string;
  toolEvents: ToolEventView[];
}

export interface AppState {
  view: "history" | "thread";
  threads: ThreadSummary[];
  activeThreadId: string;
  messages: ChatMsg[];
  streaming: StreamingBubble | null;
  thinkingStatus: string | null;
  inputEnabled: boolean;
  liveGates: LiveGateView[];
  livePlan: LivePlanView | null;
  liveReview: LiveReviewView | null;
  liveError: LiveErrorView | null;
  liveTodos: LiveTodosView | null;
  liveSessions: LiveSessionsView | null;
  // sessionId → transcript for expanded strip rows (null = fetch failed).
  sessionTranscripts: Record<string, SessionTranscriptView | null>;
  workbar: WorkbarInfo | null;
  retryStatus: RetryStatusView | null;
  tokenProgress: TokenProgressView | null;
  editFailure: { reason: string; ops: number } | null;
  liveStatus: string | null;
  // True while a controller turn / held-open controller gate is in flight (durable
  // input-disable signal from /live; survives reload). Distinct from inputEnabled,
  // which is the ephemeral per-turn flag a fresh webview mounts as `true`.
  turnActive: boolean;
  // Which kind of main turn runs (spec §5.3): a notice turn keeps the composer usable.
  turnKind: "user" | "notice" | null;
  // Queued, running or gate-parked agents in the thread — the Stop-all control (§6).
  agentsRunning: number;
  // Sticky Plan Mode toggle, hydrated from the extension's globalState on mount
  // (planModeState) and kept live as the composer/ModeGate flips it.
  planMode: boolean;
  // "Review each step" — hydrated from globalState like planMode. Local webview
  // state silently reset to true on every remount, re-enabling edit gates.
  stepReview: boolean;
  // Sub-agent roster rows (agentId → summary), merged from /live and the routes.
  agents: Record<string, AgentSummaryView>;
  // Open agents' transcripts (backfill + live events).
  agentViews: Record<string, AgentViewState>;
  // Team summaries (teamId → summary), from the routes and merged with /live.
  teams: Record<string, TeamSummaryView>;
  // Open teams' boards (backfill + live posts).
  teamViews: Record<string, TeamViewState>;
}
