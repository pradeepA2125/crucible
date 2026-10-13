import { useState, useRef, useEffect, useMemo, type ReactNode } from "react";
import { Icon } from "./Icon";
import { vscode } from "../vscodeApi";
import { EmptyState } from "./EmptyState";
import { MessageRow } from "./MessageRow";
import { AgentRow } from "./messages/AgentRow";
import { LiveSlot } from "./LiveSlot";
import { WorkBar } from "./WorkBar";
import { InputArea } from "./InputArea";
import { SettingsDrawer } from "./SettingsDrawer";
import { ChatSettingsOverlay } from "./ChatSettingsOverlay";
import { inputAvailability } from "../inputAvailability";
import type { SectionId } from "../settings/sections/meta";
import type { AppState, ChatMsg, RewindPreviewView, ToolEventView } from "../types";
import { groupTranscript } from "../transcript/groupTranscript";
import { ActivityGroup } from "./activity/ActivityGroup";
import { TurnChanges } from "./activity/TurnChanges";
import { TransientActivity } from "./activity/TransientActivity";
import { RewindDialog } from "./RewindDialog";
import { AgentsContext, type AgentsUi } from "./agents/AgentsContext";
import { AgentWindow } from "./agents/AgentWindow";
import { TeamsContext, type TeamsUi } from "./teams/TeamsContext";
import { TeamStrip } from "./teams/TeamStrip";
import { TeamWindow } from "./teams/TeamWindow";

// Gate statuses where the workbar should be HIDDEN (user is deciding something).
const WAITING_STATUSES = new Set([
  "AWAITING_PLAN_APPROVAL",
  "AWAITING_COMMAND_DECISION",
  "AWAITING_SCOPE_DECISION",
  "AWAITING_STEP_REVIEW",
  "AWAITING_VALIDATION_DECISION",
]);

/**
 * The transcript as grouped items. Live tool steps (the streaming bubble's finished tool events)
 * join the grouping as a trailing tools-only message, so work shown live sits in the very
 * group it is sealed into (same index, same key) and nothing jumps when the bubble seals.
 * `renderTail` draws the live elements after the transcript.
 */
export function TranscriptItems({ messages, turnActive, turnRunning = false, queuedIds, planVersions, onRewindRequest, streamingToolEvents, renderTail }: {
  messages: ChatMsg[]; turnActive: boolean; queuedIds: string[];
  /** The turn is still running (turnActive or input disabled): its changes card waits for the end. */
  turnRunning?: boolean;
  planVersions: Map<number, number | undefined>; onRewindRequest: (id: string) => void;
  streamingToolEvents?: ToolEventView[];
  renderTail?: () => ReactNode;
}) {
  const grouped = useMemo(() => {
    const live = streamingToolEvents ?? [];
    const all = live.length
      ? [...messages, { role: "agent", type: "text", content: "", timestamp: "", metadata: { tool_events: live } } as ChatMsg]
      : messages;
    return groupTranscript(all);
  }, [messages, streamingToolEvents]);
  const running = turnActive || turnRunning;
  // Under a running turn the changes card would sit above the live line and the streaming answer, then jump below it.
  const items = running && grouped[grouped.length - 1]?.kind === "changes" ? grouped.slice(0, -1) : grouped;
  return (
    <>
      {items.map((item) => {
        if (item.kind === "activity" || item.kind === "changes") {
          return (
            <div key={item.key} className="min-w-0">
              {item.kind === "activity" ? <ActivityGroup steps={item.steps} /> : <TurnChanges files={item.files} />}
            </div>
          );
        }
        return (
          <MessageRow key={item.msg.id ?? `m-${item.index}`} msg={item.msg} planVersion={planVersions.get(item.index)}
            turnActive={turnActive} queued={item.msg.id != null && queuedIds.includes(item.msg.id)}
            onRewindRequest={onRewindRequest} />
        );
      })}
      {renderTail?.()}
    </>
  );
}

interface Props {
  state: AppState;
  onBack: () => void;
  dismissedErrorTaskId: string | null;
  onDismissError: (taskId: string) => void;
}

/**
 * ThreadView — the main chat view.
 *
 * Layout: header | scrollable message list | LiveSlot | WorkBar | InputArea.
 * draft state is owned here so EmptyState chips can pre-fill it.
 */
export function ThreadView({ state, onBack, dismissedErrorTaskId, onDismissError }: Props) {
  const [draft, setDraft] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  // Chat rewind is local UI state, not app state: the draft lives here too, and the
  // dialog is transient. Driven by host messages, mirroring InputArea's promptExpanded
  // listener.
  const [rewindPreview, setRewindPreview] = useState<RewindPreviewView | null>(null);
  const [rewindMessageId, setRewindMessageId] = useState<string | null>(null);
  // Which settings section the floating popup is open at; null = closed.
  const [settingsSection, setSettingsSection] = useState<SectionId | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Sub-agent views (spec §10): rows expanded inline, and the one floating window.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [agentWindow, setAgentWindow] = useState<{ agentId: string; siblings: string[] } | null>(null);
  // The one open team window (spec v2 §9); its tab is "board" or a member's agentId.
  const [teamWindow, setTeamWindow] = useState<{ teamId: string; tab: string } | null>(null);

  // Another thread's agents are not ours.
  useEffect(() => {
    setExpanded(new Set());
    setAgentWindow(null);
    setTeamWindow(null);
  }, [state.activeThreadId]);

  // The host keeps one live subscription per open agent: tell it the full set.
  const teamAgent = teamWindow && teamWindow.tab !== "board" ? [teamWindow.tab] : [];
  const openAgentsKey = [...new Set([...expanded, ...(agentWindow ? [agentWindow.agentId] : []),
                                     ...teamAgent])]
    .sort().join(",");
  useEffect(() => {
    vscode.postMessage({ type: "setOpenAgents", agentIds: openAgentsKey ? openAgentsKey.split(",") : [] });
  }, [openAgentsKey]);

  const agentsUi = useMemo<AgentsUi>(() => ({
    agents: state.agents,
    views: state.agentViews,
    usage: state.threadUsage,
    expanded,
    toggleExpanded: (agentId) => setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(agentId)) next.delete(agentId);
      else next.add(agentId);
      return next;
    }),
    openWindow: (agentId, siblings) => setAgentWindow({ agentId, siblings }),
  }), [state.agents, state.agentViews, state.threadUsage, expanded]);

  const openTeamId = teamWindow?.teamId ?? "";
  useEffect(() => {
    vscode.postMessage({ type: "setOpenTeams", teamIds: openTeamId ? [openTeamId] : [] });
  }, [openTeamId]);

  const teamNames = useMemo(() => Object.fromEntries(
    Object.values(state.teams).map((t) => [t.teamId, t.name])), [state.teams]);
  const teamsUi = useMemo<TeamsUi>(() => ({
    teams: state.teams,
    views: state.teamViews,
    usage: state.threadUsage,
    openTeam: (teamId) => setTeamWindow({ teamId, tab: "board" }),
  }), [state.teams, state.teamViews, state.threadUsage]);

  useEffect(() => {
    function onHostMessage(event: MessageEvent) {
      const m = event.data as { type?: string; preview?: RewindPreviewView; text?: string; threadId?: string };
      if (m?.type === "rewindPreviewResult" && m.preview) {
        setRewindPreview(m.preview);
      } else if (m?.type === "composerPrefill" && (!m.threadId || m.threadId === state.activeThreadId)) {
        // The rewind landed: clear the dialog and hand the message back for editing.
        setRewindPreview(null);
        setRewindMessageId(null);
        setDraft(m.text ?? "");
      }
    }
    window.addEventListener("message", onHostMessage);
    return () => window.removeEventListener("message", onHostMessage);
  }, [state.activeThreadId]);

  // UX Rule 3: navLocked while input is disabled (local SSE loop appending) OR while a
  // detached controller turn is in flight — turnActive is durable across a webview reload,
  // so the lock survives where the ephemeral inputEnabled flag would reset to true.
  const navLocked = !state.inputEnabled || state.turnActive;

  const availability = inputAvailability(state);

  // WorkBar visibility: task is running, NOT waiting on user.
  const workbarVisible =
    availability.disabled &&
    !(WAITING_STATUSES.has(state.liveStatus ?? ""));

  // Filtered live error — hide if dismissed.
  const liveError =
    state.liveError && state.liveError.taskId !== dismissedErrorTaskId
      ? state.liveError
      : null;

  // Compute planVersion per plan_card message (1-indexed per task).
  // For each plan_card, planVersion = 1 + count of PRIOR plan_cards with the same taskId.
  const planVersionMap = new Map<number, number>();
  const taskPlanCounts = new Map<string, number>();
  state.messages.forEach((m, idx) => {
    if (m.type === "plan_card") {
      const tid = (m.metadata?.taskId as string) ?? m.taskId ?? "";
      const prev = taskPlanCounts.get(tid) ?? 0;
      taskPlanCounts.set(tid, prev + 1);
      planVersionMap.set(idx, prev + 1);
    }
  });

  // Finished live pills join the transcript grouping; unfinished ones belong to the transient line.
  // Memoized on the finished ids so the grouping reruns only when that set changes, not on every event.
  const liveBubble = state.streaming ?? { text: "", thinkingEntries: [], activeThinkingChunk: "", toolEvents: [] };
  const finishedKey = liveBubble.toolEvents.filter((e) => e.done).map((e) => e.id).join(",");
  const liveFinishedEvents = useMemo(
    () => liveBubble.toolEvents.filter((e) => e.done),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- finishedKey is the identity of the finished set
    [finishedKey],
  );

  // Elapsed time of the running turn. A streaming bubble re-forms after every seal (each
  // breadcrumb/diff), so keying on the bubble would reset the clock mid-turn: anchor it to the
  // turn start (turnActive false->true), falling back to the bubble start when no turn flag is set.
  const streamStartRef = useRef<number | null>(null);
  if (state.turnActive || state.streaming) {
    if (streamStartRef.current === null) streamStartRef.current = Date.now();
  } else {
    streamStartRef.current = null;
  }
  const turnRunning = state.turnActive || !state.inputEnabled;
  // A breadcrumb (e.g. "Command approved") seals the bubble while its command still runs: the
  // unfinished pill then lives in a sealed message of this turn, and the line must still name it.
  const sealedRunning = useMemo(() => {
    const running: ToolEventView[] = [];
    for (let i = state.messages.length - 1; i >= 0 && state.messages[i].role !== "user"; i--) {
      const events = (state.messages[i].metadata?.tool_events as ToolEventView[] | undefined) ?? [];
      running.unshift(...events.filter((e) => !e.done));
    }
    return running;
  }, [state.messages]);
  const transientBubble = useMemo(
    () => (sealedRunning.length ? { ...liveBubble, toolEvents: [...sealedRunning, ...liveBubble.toolEvents] } : liveBubble),
    [sealedRunning, liveBubble],
  );
  // A pending main-agent gate (not a sub-agent's or team's card) means the user, not a tool, is being waited on.
  const waitingOnGate = state.liveGates.some((g) => !g.agent && !g.team);
  // Streaming answer text is the live signal once nothing is running.
  const showTransient = !state.retryStatus && turnRunning
    && !(transientBubble.text && !transientBubble.toolEvents.some((e) => !e.done));
  const streamStartedAt = streamStartRef.current ?? Date.now();

  // Auto-scroll on new messages / streaming updates.
  // Guard: scrollIntoView is not available in jsdom (tests).
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [
    state.messages.length,
    state.streaming?.text,
    state.streaming?.toolEvents.length,
    state.streaming?.thinkingEntries.length,
    state.thinkingStatus,
    state.retryStatus?.message,
  ]);

  const activeThread = state.threads.find(
    (t) => t.threadId === state.activeThreadId,
  );
  const threadTitle = activeThread?.title ?? "Chat";

  const isEmpty =
    state.messages.length === 0 &&
    !state.streaming &&
    !state.thinkingStatus &&
    !state.retryStatus;

  return (
    <AgentsContext.Provider value={agentsUi}>
    <TeamsContext.Provider value={teamsUi}>
    <div className="relative flex h-full overflow-hidden">
      {/* Settings drawer — squeezes the chat column (inline sibling, no overlay). */}
      <SettingsDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onSelect={(section) => {
          setSettingsSection(section);
          setDrawerOpen(false);
        }}
      />

      {/* ── Chat column ── */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      {/* ── Header ── */}
      <div
        className="flex items-center gap-2 px-2 py-2 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--color-border)" }}
      >
        {/* Settings drawer toggle (☰) — available even during a turn. */}
        <button
          type="button"
          onClick={() => setDrawerOpen((o) => !o)}
          ref={settingsButtonRef}
          aria-label="Settings menu"
          title="Settings"
          className={[
            "flex items-center justify-center w-6 h-6 rounded-control",
            "border transition-colors duration-150",
          ].join(" ")}
          style={{ color: "var(--color-text-3)", background: "transparent", borderColor: "transparent" }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "var(--accent-bg)";
            (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--accent-brd)";
            (e.currentTarget as HTMLButtonElement).style.color = "var(--color-accent)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "transparent";
            (e.currentTarget as HTMLButtonElement).style.borderColor = "transparent";
            (e.currentTarget as HTMLButtonElement).style.color = "var(--color-text-3)";
          }}
        >
          <Icon name="gear" size={14} />
        </button>

        {/* Memory Inspector shortcut — opens the standalone panel (crucible.openMemoryPanel),
            which already degrades gracefully if CRUCIBLE_MEMORY_ENABLED is off. */}
        <button
          type="button"
          onClick={() => vscode.postMessage({ type: "openMemoryPanel" })}
          aria-label="Memory Inspector"
          title="Memory Inspector"
          className={[
            "flex items-center justify-center w-6 h-6 rounded-control",
            "border transition-colors duration-150",
          ].join(" ")}
          style={{ color: "var(--color-text-3)", background: "transparent", borderColor: "transparent" }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "var(--accent-bg)";
            (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--accent-brd)";
            (e.currentTarget as HTMLButtonElement).style.color = "var(--color-accent)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "transparent";
            (e.currentTarget as HTMLButtonElement).style.borderColor = "transparent";
            (e.currentTarget as HTMLButtonElement).style.color = "var(--color-text-3)";
          }}
        >
          <Icon name="db" size={14} />
        </button>

        {/* Stop every agent in the thread (spec §4.4, §6) — shown while any runs. */}
        {state.agentsRunning > 0 && (
          <button
            type="button"
            onClick={() => vscode.postMessage({ type: "stopAllAgents" })}
            aria-label="Stop all agents"
            title="Stop all agents"
            className="flex items-center justify-center w-6 h-6 rounded-control border transition-colors duration-150"
            style={{ color: "var(--color-red)", background: "transparent", borderColor: "transparent" }}
          >
            <Icon name="stop" size={14} />
          </button>
        )}

        {/* AXON dependency space — 3D knowledge-graph panel (crucible.openGraphPanel);
            degrades to an empty state when no index snapshot exists. */}
        <button
          type="button"
          onClick={() => vscode.postMessage({ type: "openGraphPanel" })}
          aria-label="Dependency Space"
          title="Dependency Space"
          className={[
            "flex items-center justify-center w-6 h-6 rounded-control",
            "border transition-colors duration-150",
          ].join(" ")}
          style={{ color: "var(--color-text-3)", background: "transparent", borderColor: "transparent" }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "var(--accent-bg)";
            (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--accent-brd)";
            (e.currentTarget as HTMLButtonElement).style.color = "var(--color-accent)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "transparent";
            (e.currentTarget as HTMLButtonElement).style.borderColor = "transparent";
            (e.currentTarget as HTMLButtonElement).style.color = "var(--color-text-3)";
          }}
        >
          <Icon name="orbit" size={14} />
        </button>

        {/* Back button */}
        <button
          type="button"
          onClick={() => { if (!navLocked) onBack(); }}
          disabled={navLocked}
          title={navLocked ? "A turn is in progress" : undefined}
          aria-label="Back to history"
          className={[
            "flex items-center justify-center w-6 h-6 rounded-control",
            "border transition-colors duration-150",
            "disabled:opacity-40 disabled:cursor-not-allowed",
          ].join(" ")}
          style={{
            color: "var(--color-accent)",
            background: "transparent",
            borderColor: "transparent",
          }}
          onMouseEnter={(e) => {
            if (!navLocked) {
              (e.currentTarget as HTMLButtonElement).style.background =
                "var(--accent-bg)";
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "var(--accent-brd)";
            }
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background =
              "transparent";
            (e.currentTarget as HTMLButtonElement).style.borderColor =
              "transparent";
          }}
        >
          <Icon name="chev-l" size={14} />
        </button>

        {/* Thread title */}
        <span className="flex-1 min-w-0 truncate text-meta text-text-2">
          {threadTitle}
        </span>

        {/* New chat button */}
        <button
          type="button"
          onClick={() => { if (!navLocked) vscode.postMessage({ type: "newChat" }); }}
          disabled={navLocked}
          title={navLocked ? "A turn is in progress" : undefined}
          aria-label="New chat"
          className={[
            "flex items-center justify-center w-6 h-6 rounded-control",
            "border transition-colors duration-150",
            "disabled:opacity-40 disabled:cursor-not-allowed",
          ].join(" ")}
          style={{
            color: "var(--color-text-3)",
            background: "transparent",
            borderColor: "transparent",
          }}
          onMouseEnter={(e) => {
            if (!navLocked) {
              (e.currentTarget as HTMLButtonElement).style.background =
                "var(--accent-bg)";
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "var(--accent-brd)";
              (e.currentTarget as HTMLButtonElement).style.color =
                "var(--color-accent)";
            }
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background =
              "transparent";
            (e.currentTarget as HTMLButtonElement).style.borderColor =
              "transparent";
            (e.currentTarget as HTMLButtonElement).style.color =
              "var(--color-text-3)";
          }}
        >
          <Icon name="plus" size={13} />
        </button>
      </div>

      {/* ── Message list ──
          [&>*]:flex-shrink-0 is load-bearing: children with overflow-hidden
          (diff/plan/review cards) have a flex automatic minimum size of ZERO,
          so once the list overflows they silently collapse to a border line
          while plain text rows keep their height. */}
      <div className="flex-1 overflow-y-auto py-3 [&>*]:flex-shrink-0">
        <div data-testid="transcript-column" className="mx-auto flex w-full max-w-[760px] flex-col gap-3 px-4 [&>*]:flex-shrink-0">
        {isEmpty ? (
          <EmptyState onPick={setDraft} />
        ) : (
          <>
            <TranscriptItems messages={state.messages} turnActive={state.turnActive} turnRunning={turnRunning}
              queuedIds={state.queuedIds} planVersions={planVersionMap} onRewindRequest={setRewindMessageId}
              streamingToolEvents={liveFinishedEvents}
              renderTail={() => (
                <>
                  {/* Retry-status bubble — takes precedence over both the thinking
                      status line and the streaming bubble below while a retry is
                      in flight. Ephemeral: never appended to thinkingEntries, no
                      trace left once it clears (see design spec). */}
                  {state.retryStatus && (
                    <div
                      className="flex items-center gap-2 text-caption"
                      style={{ color: "var(--color-text-3)", animation: "pulse 1.5s ease-in-out infinite" }}
                    >
                      <span
                        className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                        style={{ background: "var(--color-accent)" }}
                        aria-hidden="true"
                      />
                      {state.retryStatus.message}
                    </div>
                  )}

                  {/* Transient line (hidden while a retry is in flight — resumes once it clears) */}
                  {showTransient && (
                    <TransientActivity bubble={transientBubble} startedAt={streamStartedAt}
                      fallback={state.thinkingStatus} waiting={waitingOnGate} />
                  )}
                  {!state.retryStatus && state.streaming?.text && (
                    <AgentRow content={state.streaming.text} streaming hideThinkingBlock />
                  )}
                </>
              )} />
          </>
        )}

        {/* Scroll anchor */}
        <div ref={bottomRef} />
        </div>
      </div>

      {/* ── Pinned bottom section ── */}
      <LiveSlot
        liveGates={state.liveGates}
        livePlan={state.livePlan}
        liveReview={state.liveReview}
        liveError={liveError}
        liveTodos={state.liveTodos}
        providerAccess={state.providerAccess}
        liveSessions={state.liveSessions}
        sessionTranscripts={state.sessionTranscripts}
        onExpandSession={(sessionId) => vscode.postMessage({ type: "fetchSessionTranscript", sessionId })}
        onDismissError={() => state.liveError && onDismissError(state.liveError.taskId)}
      />

      <TeamStrip />

      <WorkBar
        workbar={state.workbar}
        liveStatus={state.liveStatus}
        thinkingStatus={state.thinkingStatus}
        tokenProgress={state.tokenProgress}
        visible={workbarVisible}
      />

      <div className="mx-auto w-full max-w-[760px] px-4 pb-3 pt-2 flex-shrink-0">
        <InputArea
          threadId={state.activeThreadId}
          availability={availability}
          draft={draft}
          onDraftChange={setDraft}
          approval={state.approval}
          planMode={state.planMode}
          stepReview={state.stepReview}
          threadUsage={state.threadUsage}
          contextUsage={state.contextUsage}
          contextUsageStale={state.contextUsageStale}
          teamNames={teamNames}
        />
      </div>
      </div>

      {/* Floating settings popup — over the whole chat, opened at the picked section. */}
      {settingsSection !== null && (
        <ChatSettingsOverlay returnTo={settingsButtonRef} section={settingsSection} onClose={() => setSettingsSection(null)} />
      )}

      {/* Confirm gate for a permanent rewind. */}
      {rewindPreview !== null && (
        <RewindDialog
          preview={rewindPreview}
          onCancel={() => {
            setRewindPreview(null);
            setRewindMessageId(null);
          }}
          onConfirm={() => {
            if (rewindMessageId) {
              vscode.postMessage({ type: "rewindConfirm", messageId: rewindMessageId });
            }
            setRewindPreview(null);
          }}
          onStopAllAgents={() => {
            vscode.postMessage({ type: "stopAllAgents" });
            // Ask again: the dialog clears its refusal once the agents have stopped.
            if (rewindMessageId) {
              vscode.postMessage({ type: "rewindPreview", messageId: rewindMessageId });
            }
          }}
        />
      )}

      {agentWindow !== null && (
        <AgentWindow
          agentId={agentWindow.agentId}
          siblings={agentWindow.siblings}
          onSwitch={(agentId) => setAgentWindow({ ...agentWindow, agentId })}
          onClose={() => setAgentWindow(null)}
        />
      )}

      {teamWindow !== null && (
        <TeamWindow
          teamId={teamWindow.teamId}
          tab={teamWindow.tab}
          onTab={(tab) => setTeamWindow({ ...teamWindow, tab })}
          onClose={() => setTeamWindow(null)}
        />
      )}
    </div>
    </TeamsContext.Provider>
    </AgentsContext.Provider>
  );
}
