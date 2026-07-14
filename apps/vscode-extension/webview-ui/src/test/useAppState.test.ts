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
});
