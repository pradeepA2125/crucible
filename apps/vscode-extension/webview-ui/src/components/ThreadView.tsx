import { useState, useRef, useEffect } from "react";
import { Icon } from "./Icon";
import { vscode } from "../vscodeApi";
import { EmptyState } from "./EmptyState";
import { MessageRow } from "./MessageRow";
import { AgentRow } from "./messages/AgentRow";
import { LiveSlot } from "./LiveSlot";
import { WorkBar } from "./WorkBar";
import { InputArea } from "./InputArea";
import { inputAvailability } from "../inputAvailability";
import type { AppState, ChatMsg } from "../types";

// Gate statuses where the workbar should be HIDDEN (user is deciding something).
const WAITING_STATUSES = new Set([
  "AWAITING_PLAN_APPROVAL",
  "AWAITING_COMMAND_DECISION",
  "AWAITING_SCOPE_DECISION",
  "AWAITING_STEP_REVIEW",
  "AWAITING_VALIDATION_DECISION",
]);

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
  const bottomRef = useRef<HTMLDivElement>(null);

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
  ]);

  const activeThread = state.threads.find(
    (t) => t.threadId === state.activeThreadId,
  );
  const threadTitle = activeThread?.title ?? "Chat";

  const isEmpty =
    state.messages.length === 0 &&
    !state.streaming &&
    !state.thinkingStatus;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Header ── */}
      <div
        className="flex items-center gap-2 px-2 py-2 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--color-border)" }}
      >
        {/* Back button */}
        <button
          type="button"
          onClick={() => { if (!navLocked) onBack(); }}
          disabled={navLocked}
          title={navLocked ? "A turn is in progress" : undefined}
          aria-label="Back to history"
          className={[
            "flex items-center justify-center w-6 h-6 rounded-md",
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
        <span className="flex-1 min-w-0 truncate text-xs text-text-2">
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
            "flex items-center justify-center w-6 h-6 rounded-md",
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
      <div className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-3 [&>*]:flex-shrink-0">
        {isEmpty ? (
          <EmptyState onPick={setDraft} />
        ) : (
          <>
            {state.messages.map((m: ChatMsg, i: number) => (
              <MessageRow key={i} msg={m} planVersion={planVersionMap.get(i)} />
            ))}

            {/* Thinking status line (when no streaming bubble yet) */}
            {state.thinkingStatus && !state.streaming && (
              <div className="flex items-center gap-2 text-[11px]"
                style={{ color: "var(--color-text-3)" }}>
                <span
                  className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                  style={{
                    background: "var(--color-accent)",
                    animation: "pulse 1.5s ease-in-out infinite",
                  }}
                  aria-hidden="true"
                />
                {state.thinkingStatus}
              </div>
            )}

            {/* Streaming bubble */}
            {state.streaming && (
              <AgentRow
                content={state.streaming.text}
                streaming
                streamingThinkingEntries={state.streaming.thinkingEntries}
                streamingThinkingChunk={state.streaming.activeThinkingChunk}
                toolEvents={state.streaming.toolEvents}
              />
            )}
          </>
        )}

        {/* Scroll anchor */}
        <div ref={bottomRef} />
      </div>

      {/* ── Pinned bottom section ── */}
      <LiveSlot
        liveGate={state.liveGate}
        livePlan={state.livePlan}
        liveReview={state.liveReview}
        liveError={liveError}
        liveTodos={state.liveTodos}
        onDismissError={() => state.liveError && onDismissError(state.liveError.taskId)}
      />

      <WorkBar
        workbar={state.workbar}
        liveStatus={state.liveStatus}
        thinkingStatus={state.thinkingStatus}
        visible={workbarVisible}
      />

      <div className="px-3 pb-3 pt-2 flex-shrink-0">
        <InputArea
          availability={availability}
          draft={draft}
          onDraftChange={setDraft}
        />
      </div>
    </div>
  );
}
