import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import type { ExtensionMessage } from "../types";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

// Import AFTER mock is set up.
import { useAppState } from "../hooks/useAppState";

// ── Helper ───────────────────────────────────────────────────────────────────

function fireMessage(data: ExtensionMessage): void {
  window.dispatchEvent(new MessageEvent("message", { data }));
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("useAppState", () => {
  // 1. renderThreadList
  it("renderThreadList populates threads and activeThreadId", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "renderThreadList",
        threads: [{ threadId: "t1", title: "Thread 1", createdAt: "2024-01-01" }],
        activeThreadId: "t1",
      });
    });

    expect(result.current.state.threads).toHaveLength(1);
    expect(result.current.state.threads[0].threadId).toBe("t1");
    expect(result.current.state.activeThreadId).toBe("t1");
  });

  // 2. plan_card dedup
  it("plan_card dedup: identical content collapses; new content appends as second version", () => {
    const { result } = renderHook(() => useAppState());

    const planMsg: ExtensionMessage = {
      type: "appendMessage",
      message: {
        role: "agent",
        content: "## Plan\n- Step 1",
        type: "plan_card",
        taskId: "task-1",
        timestamp: "t",
        metadata: { taskId: "task-1" },
      },
    };

    // Fire the same plan twice — should collapse to one.
    act(() => { fireMessage(planMsg); });
    act(() => { fireMessage(planMsg); });

    expect(result.current.state.messages.filter((m) => m.type === "plan_card")).toHaveLength(1);

    // Fire a plan with different content — should append as a second version.
    act(() => {
      fireMessage({
        type: "appendMessage",
        message: {
          role: "agent",
          content: "## Plan\n- Step 1\n- Step 2",
          type: "plan_card",
          taskId: "task-1",
          timestamp: "t2",
          metadata: { taskId: "task-1" },
        },
      });
    });

    expect(result.current.state.messages.filter((m) => m.type === "plan_card")).toHaveLength(2);
  });

  // 3. streaming chunks accumulate
  it("streaming chunks accumulate correctly", () => {
    const { result } = renderHook(() => useAppState());

    act(() => { fireMessage({ type: "appendChunk", chunk: "Hello" }); });
    act(() => { fireMessage({ type: "appendChunk", chunk: " world" }); });

    expect(result.current.state.streaming?.text).toBe("Hello world");
  });

  // 4. finalizeAgentMessage seals the bubble
  it("finalizeAgentMessage seals the streaming bubble into a persisted agent message", () => {
    const { result } = renderHook(() => useAppState());

    act(() => { fireMessage({ type: "appendChunk", chunk: "Done" }); });
    act(() => { fireMessage({ type: "finalizeAgentMessage" }); });

    expect(result.current.state.streaming).toBeNull();
    const msgs = result.current.state.messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].role).toBe("agent");
    expect(msgs[0].content).toBe("Done");
    expect(msgs[0].type).toBe("text");
    // The sealed message must carry the caller-supplied timestamp (non-empty ISO string).
    expect(typeof (msgs[0] as { timestamp: string }).timestamp).toBe("string");
    expect((msgs[0] as { timestamp: string }).timestamp).not.toBe("");
  });

  // 5. resolveInlineChangeCard patches metadata.resolved
  it("resolveInlineChangeCard patches metadata.resolved of the matching diff_card", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "appendMessage",
        message: {
          role: "agent",
          content: "",
          type: "diff_card",
          taskId: "inline-task-42",
          timestamp: "t",
          metadata: { taskId: "inline-task-42" },
        },
      });
    });

    act(() => {
      fireMessage({ type: "resolveInlineChangeCard", taskId: "inline-task-42", resolution: "applied" });
    });

    const card = result.current.state.messages.find((m) => m.type === "diff_card");
    expect(card?.metadata.resolved).toBe("applied");
  });

  // 6. thinking chunk-then-entry preserves BOTH
  it("appendThinkingChunk followed by appendThinkingEntry preserves both", () => {
    const { result } = renderHook(() => useAppState());

    act(() => { fireMessage({ type: "appendThinkingChunk", chunk: "loading weights" }); });
    act(() => { fireMessage({ type: "appendThinkingEntry", text: "classified intent" }); });

    expect(result.current.state.streaming?.thinkingEntries).toEqual([
      "loading weights",
      "classified intent",
    ]);
    expect(result.current.state.streaming?.activeThinkingChunk).toBe("");
  });

  // 7. tool event pairing
  it("tool event pairing: appendToolResult marks the matching event done", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "appendToolEvent",
        event: { id: 1, tool: "read_file", args: { path: "a.ts" }, source: "execution" },
      });
    });
    act(() => {
      fireMessage({ type: "appendToolResult", id: 1, output: "line1", isError: false });
    });
    act(() => {
      fireMessage({
        type: "appendToolEvent",
        event: { id: 2, tool: "search_code", args: { query: "fn" }, source: "execution" },
      });
    });

    const events = result.current.state.streaming?.toolEvents ?? [];
    expect(events).toHaveLength(2);
    expect(events[0].done).toBe(true);
    expect(events[0].output).toBe("line1");
    expect(events[1].done).toBe(false);
  });

  // 8. plan_card messages do NOT seal-append as text
  it("appendMessage plan_card does not generate a phantom text message", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "appendMessage",
        message: {
          role: "agent",
          content: "## Plan\n- Step 1",
          type: "plan_card",
          taskId: "task-2",
          timestamp: "t",
          metadata: { taskId: "task-2" },
        },
      });
    });

    expect(result.current.state.messages).toHaveLength(1);
    expect(result.current.state.messages[0].type).toBe("plan_card");
  });

  // 9. updateWorkbar + liveStatus
  it("updateWorkbar sets and clears workbar; liveStatus sets liveStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({ type: "updateWorkbar", info: { stepIndex: 1, totalSteps: 3, stepTitle: "Step 1" } });
    });
    expect(result.current.state.workbar).toMatchObject({ stepIndex: 1, totalSteps: 3 });

    act(() => { fireMessage({ type: "updateWorkbar", info: null }); });
    expect(result.current.state.workbar).toBeNull();

    act(() => { fireMessage({ type: "liveStatus", status: "EXECUTING" }); });
    expect(result.current.state.liveStatus).toBe("EXECUTING");
  });

  // 9b. liveStatus reconciliation: a controller turn that ended (turnActive=false, no task)
  // re-enables the composer even when NO streaming bubble lingered — the missed-chat_done
  // wedge where inputEnabled is stuck false and there's nothing to seal.
  it("liveStatus(turnActive=false, status=null) re-enables input with no streaming bubble", () => {
    const { result } = renderHook(() => useAppState());

    // Turn start: input disabled, no bubble yet (e.g. error before any broadcast).
    act(() => { fireMessage({ type: "setInputEnabled", enabled: false }); });
    expect(result.current.state.inputEnabled).toBe(false);
    expect(result.current.state.streaming).toBeNull();

    // /live reports the controller turn ended.
    act(() => { fireMessage({ type: "liveStatus", status: null, turnActive: false }); });
    expect(result.current.state.inputEnabled).toBe(true);
    expect(result.current.state.turnActive).toBe(false);
  });

  // 9c. liveStatus reconciliation: a lingering streaming bubble is SEALED (no data loss)
  // and input re-enabled when the turn ends.
  it("liveStatus(turnActive=false, status=null) seals a lingering bubble and re-enables", () => {
    const { result } = renderHook(() => useAppState());

    act(() => { fireMessage({ type: "setInputEnabled", enabled: false }); });
    act(() => { fireMessage({ type: "appendChunk", chunk: "partial answer" }); });
    expect(result.current.state.streaming).not.toBeNull();

    act(() => { fireMessage({ type: "liveStatus", status: null, turnActive: false }); });
    // Bubble sealed into a persisted message (text preserved), input re-enabled.
    expect(result.current.state.streaming).toBeNull();
    expect(result.current.state.inputEnabled).toBe(true);
    expect(result.current.state.messages.at(-1)?.content).toBe("partial answer");
  });

  // 9d. Guard: during TASK execution (status is a task status, turnActive=false), this
  // branch must NOT force input — task input is governed by liveStatus precedence, not here.
  it("liveStatus with a task status does not force-enable input", () => {
    const { result } = renderHook(() => useAppState());

    act(() => { fireMessage({ type: "setInputEnabled", enabled: false }); });
    act(() => { fireMessage({ type: "liveStatus", status: "EXECUTING", turnActive: false }); });
    expect(result.current.state.inputEnabled).toBe(false); // unchanged — task governs it
    expect(result.current.state.liveStatus).toBe("EXECUTING");
  });

  // 10. finalizeAgentMessage with open activeThinkingChunk seals it as a thinking_log entry
  it("finalize with open activeThinkingChunk seals it as a final thinking_log entry in metadata", () => {
    const { result } = renderHook(() => useAppState());

    act(() => { fireMessage({ type: "appendThinkingChunk", chunk: "reasoning step" }); });
    act(() => { fireMessage({ type: "appendChunk", chunk: "Answer" }); });
    act(() => { fireMessage({ type: "finalizeAgentMessage" }); });

    const msg = result.current.state.messages[0];
    expect(msg.metadata.thinking_log).toEqual(["reasoning step"]);
    expect(msg.content).toBe("Answer");
  });

  it("ignores malformed/foreign window messages without crashing", () => {
    const { result } = renderHook(() => useAppState());
    const fireRaw = (data: unknown) =>
      window.dispatchEvent(new MessageEvent("message", { data }));
    act(() => {
      fireRaw(undefined);
      fireRaw(null);
      fireRaw("a string");
      fireRaw({ noType: true });
      fireRaw({ type: 42 });
    });
    // State untouched, no throw.
    expect(result.current.state.messages).toHaveLength(0);
    expect(result.current.state.threads).toHaveLength(0);
  });

  // appendToolEvent dedups against a loaded in-flight message (switch-back-to-active-turn):
  // the resumed live stream replays already-persisted pills (same call_index id) — those
  // must be skipped, while genuinely new pills still render in the streaming bubble.
  it("appendToolEvent skips a pill already in a loaded in-flight message; adds a new one", () => {
    const { result } = renderHook(() => useAppState());
    act(() => {
      fireMessage({
        type: "appendMessage",
        message: {
          role: "agent", content: "", type: "text", timestamp: "t",
          metadata: {
            inflight_turn_id: "turn-1",
            tool_events: [{ id: 5, tool: "read_file", args: {}, source: "execution", done: true }],
          },
        },
      });
    });
    // Replayed pill (same id 5) → deduped, no streaming pill.
    act(() => {
      fireMessage({
        type: "appendToolEvent",
        event: { id: 5, tool: "read_file", args: {}, source: "execution" },
      });
    });
    expect(result.current.state.streaming?.toolEvents ?? []).toHaveLength(0);
    // New pill (id 99) → renders in the bubble.
    act(() => {
      fireMessage({
        type: "appendToolEvent",
        event: { id: 99, tool: "search_code", args: {}, source: "execution" },
      });
    });
    expect((result.current.state.streaming?.toolEvents ?? []).map((t) => t.id)).toEqual([99]);
  });

  // ── retryStatus ──────────────────────────────────────────────────────────

  it("updateRetryStatus sets retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "rate_limited", message: "⏳ retrying…" },
      });
    });

    expect(result.current.state.retryStatus).toEqual({
      attempt: 1, max_attempts: 4, reason: "rate_limited", message: "⏳ retrying…",
    });
  });

  it("retryStatus never lands in thinkingEntries", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "appendChunk", chunk: "real answer" });
      fireMessage({ type: "finalizeAgentMessage" });
    });

    const last = result.current.state.messages[result.current.state.messages.length - 1];
    const thinkingLog = (last.metadata?.thinking_log as string[] | undefined) ?? [];
    expect(thinkingLog.some((t) => t.includes("retrying"))).toBe(false);
  });

  it("appendChunk (real content) clears retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "appendChunk", chunk: "hi" });
    });

    expect(result.current.state.retryStatus).toBeNull();
  });

  it("appendThinkingChunk (real progress resuming) clears retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "appendThinkingChunk", chunk: "real reasoning" });
    });

    expect(result.current.state.retryStatus).toBeNull();
  });

  it("clearThread clears retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "clearThread" });
    });

    expect(result.current.state.retryStatus).toBeNull();
  });

  it("liveStatus controllerTurnEnded clears retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({ type: "setInputEnabled", enabled: false });
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "liveStatus", status: null, turnActive: false });
    });

    expect(result.current.state.retryStatus).toBeNull();
  });

  // ── stepReview ────────────────────────────────────────────────────────────
  // BUG: "Review each step" was local useState(true) in InputArea, while planMode was
  // a prop hydrated from globalState. Every remount (thread switch, panel reload)
  // silently returned it to CHECKED, so an unchecked box came back on and the next
  // message gated its edits again. It has to be state like planMode.

  it("stepReview defaults to true", () => {
    const { result } = renderHook(() => useAppState());
    expect(result.current.state.stepReview).toBe(true);
  });

  it("reviewPrefState sets stepReview and survives other messages", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({ type: "reviewPrefState", enabled: false });
    });
    expect(result.current.state.stepReview).toBe(false);

    // A remount is what used to reset it; here, an unrelated message must not.
    act(() => {
      fireMessage({ type: "showThinking", message: "Thinking…" });
    });
    expect(result.current.state.stepReview).toBe(false);
  });

  // ── tokenProgress ─────────────────────────────────────────────────────────
  // BUG: tokenProgress was cleared alongside retryStatus/editFailure in every
  // activity reducer. Those two are one-off NOTICES that should yield to real
  // progress; tokenProgress is a CONCURRENT counter of that very progress.
  // Reasoning deltas land at ~29/sec (each → appendThinkingChunk → null) while
  // token_progress is throttled to ~6.7/sec, so the counter was nulled ~4x for
  // every time it was set — a strobe, not a readable number.

  const PROGRESS = { thinking: 147, output: 32, input: 372_000, exact: false };

  it.each([
    ["appendThinkingChunk", { type: "appendThinkingChunk", chunk: "reasoning" }],
    ["appendThinkingEntry", { type: "appendThinkingEntry", text: "classified intent" }],
    ["appendChunk", { type: "appendChunk", chunk: "hi" }],
    ["showThinking", { type: "showThinking", message: "Thinking…" }],
    [
      "appendToolEvent",
      {
        type: "appendToolEvent",
        event: { id: "tc-1", tool: "read_file", args: {}, source: "execution" },
      },
    ],
  ] as [string, ExtensionMessage][])(
    "%s does not clear tokenProgress (counter must not strobe)",
    (_name, activity) => {
      const { result } = renderHook(() => useAppState());

      act(() => {
        fireMessage({ type: "updateTokenProgress", progress: PROGRESS });
        fireMessage(activity);
      });

      expect(result.current.state.tokenProgress).toEqual(PROGRESS);
    },
  );

  it("clearThread clears tokenProgress", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({ type: "updateTokenProgress", progress: PROGRESS });
      fireMessage({ type: "clearThread" });
    });

    expect(result.current.state.tokenProgress).toBeNull();
  });

  it("liveStatus controllerTurnEnded KEEPS tokenProgress (final count must stay readable)", () => {
    // Reversed deliberately. The closing tick carries the provider's exact usage —
    // the only non-estimated number in the whole call. Clearing it at turn end
    // unmounted it the instant it became correct, so the figure flashed and vanished
    // and "what did that turn cost?" had no answer. The host now clears at the NEXT
    // turn's start, which still prevents a stale count carrying across turns.
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({ type: "setInputEnabled", enabled: false });
      fireMessage({ type: "updateTokenProgress", progress: PROGRESS });
      fireMessage({ type: "liveStatus", status: null, turnActive: false });
    });

    expect(result.current.state.tokenProgress).toEqual(PROGRESS);
  });

  it("merges roster rows, opens an agent view, folds its events, and resets on clearThread", () => {
    const { result } = renderHook(() => useAppState());
    const row = {
      agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
      status: "running", now: "read_file a.py", toolCount: 1, filesChangedCount: 0,
      startedAt: null, endedAt: null, reportPreview: "",
    };
    act(() => { fireMessage({ type: "renderAgents", agents: [row] }); });
    act(() => { fireMessage({ type: "renderAgents", agents: [{ ...row, toolCount: 2 }] }); });
    expect(result.current.state.agents["agent-a"].toolCount).toBe(2);

    act(() => { fireMessage({ type: "agentDetail", agentId: "agent-a", detail: {
      ...row, prompt: "Survey the code", report: "", filesChanged: [], staleRefusals: 0,
      transcript: [], lastSeq: 3 } }); });
    act(() => { fireMessage({ type: "agentEvent", agentId: "agent-a", event: {
      type: "tool_call", payload: { tool: "read_file", args: {}, call_index: 0 }, seq: 4 } }); });
    expect(result.current.state.agentViews["agent-a"].live).toHaveLength(1);

    act(() => { fireMessage({ type: "clearThread" }); });
    expect(result.current.state.agents).toEqual({});
    expect(result.current.state.agentViews).toEqual({});
  });

  it("tracks team summaries, live merges, boards and clears them with the thread", () => {
    const { result } = renderHook(() => useAppState());
    const team = { teamId: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 1,
      maxRounds: 3, pausedReason: null, members: [], openProposals: [],
      usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z" };
    const post = { teamId: "team-1", seq: 1, author: "main", kind: "proposal", recipient: null,
      text: "plan", mentions: [], refId: null, round: 0, payload: {}, closed: null,
      createdAt: "2026-10-05T00:00:00Z" };
    act(() => { fireMessage({ type: "renderTeams", teams: [team] }); });
    act(() => { fireMessage({ type: "renderLiveTeams", teams: [{ teamId: "team-1",
      name: "auth", phase: "DELIBERATING", round: 2, maxRounds: 3, pausedReason: null,
      members: [] }] }); });
    expect(result.current.state.teams["team-1"]).toMatchObject({ round: 2, goal: "g" });
    act(() => { fireMessage({ type: "teamDetail", teamId: "team-1",
      detail: { ...team, posts: [post], lastSeq: 1, activity: [], lastAseq: 0 } }); });
    act(() => { fireMessage({ type: "teamEvent", teamId: "team-1",
      event: { type: "team_post", post: { ...post, seq: 2, kind: "post", text: "hi" } } }); });
    act(() => { fireMessage({ type: "teamEvent", teamId: "team-1",
      event: { type: "team_phase", phase: "DISBANDED", round: 2, pausedReason: null } }); });
    expect(result.current.state.teamViews["team-1"].posts.map((p) => p.seq)).toEqual([1, 2]);
    expect(result.current.state.teams["team-1"].phase).toBe("DISBANDED");
    act(() => { fireMessage({ type: "clearThread" }); });
    expect(result.current.state.teams).toEqual({});
    expect(result.current.state.teamViews).toEqual({});
  });

  it("appends a roster message once even when it is delivered twice", () => {
    const { result } = renderHook(() => useAppState());
    const message = { role: "agent" as const, content: "", type: "agent_dispatch" as const,
      timestamp: "2026-10-01T00:00:00Z", metadata: { agent_ids: ["agent-a"] } };
    act(() => { fireMessage({ type: "appendMessage", message }); });
    act(() => { fireMessage({ type: "appendMessage", message }); });
    expect(result.current.state.messages.filter((m) => m.type === "agent_dispatch")).toHaveLength(1);
  });
});

describe("useAppState — background agents (spec §6)", () => {
  const msg = (id: string) => ({ role: "agent" as const, content: id, type: "text" as const,
                                 id, timestamp: "2026-10-04T00:00:00Z", metadata: {} });

  it("replaceMessages swaps the transcript and keeps the roster", () => {
    const { result } = renderHook(() => useAppState());
    act(() => { fireMessage({ type: "appendMessage", message: msg("a") }); });
    act(() => { fireMessage({ type: "renderAgents", agents: [{
      agentId: "x", parentAgentId: null, depth: 1, name: "explore", label: "x",
      status: "running", now: "", toolCount: 0, filesChangedCount: 0, startedAt: null,
      endedAt: null, reportPreview: "" }] }); });
    const agents = result.current.state.agents;
    act(() => { fireMessage({ type: "replaceMessages", messages: [msg("a"), msg("b")] }); });
    expect(result.current.state.messages.map((m) => m.id)).toEqual(["a", "b"]);
    expect(result.current.state.agents).toBe(agents);
  });

  it("removeChatMessage drops one message by id", () => {
    const { result } = renderHook(() => useAppState());
    act(() => { fireMessage({ type: "appendMessage", message: msg("a") }); });
    act(() => { fireMessage({ type: "appendMessage", message: msg("b") }); });
    act(() => { fireMessage({ type: "removeChatMessage", id: "a" }); });
    expect(result.current.state.messages.map((m) => m.id)).toEqual(["b"]);
  });

  it("a notice marker delivered twice renders once", () => {
    const { result } = renderHook(() => useAppState());
    const notice = { ...msg("n1"), type: "notice" as const, content: "🔔 done" };
    act(() => { fireMessage({ type: "appendMessage", message: notice }); });
    act(() => { fireMessage({ type: "appendMessage", message: notice }); });
    expect(result.current.state.messages.filter((m) => m.type === "notice")).toHaveLength(1);
  });

  it("liveStatus carries the turn kind and the running-agent count", () => {
    const { result } = renderHook(() => useAppState());
    act(() => { fireMessage({ type: "liveStatus", status: null, turnActive: true,
                              turnKind: "notice", agentsRunning: 2 }); });
    expect([result.current.state.turnKind, result.current.state.agentsRunning])
      .toEqual(["notice", 2]);
  });

  // Queued sends (spec v2 §5.3): tagged until the notice turn takes the message.
  it("markQueued tags a message until the turn stops being a notice turn", () => {
    const { result } = renderHook(() => useAppState());
    act(() => { fireMessage({ type: "markQueued", id: "m1" }); });
    expect(result.current.state.queuedIds).toEqual(["m1"]);
    act(() => { fireMessage({ type: "liveStatus", status: null, turnActive: true, turnKind: "notice" }); });
    expect(result.current.state.queuedIds).toEqual(["m1"]);
    act(() => { fireMessage({ type: "liveStatus", status: null, turnActive: true, turnKind: "user" }); });
    expect(result.current.state.queuedIds).toEqual([]);
  });
});
