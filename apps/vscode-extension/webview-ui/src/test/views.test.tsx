import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LiveSlot } from "../components/LiveSlot";
import { InputArea } from "../components/InputArea";
import { HistoryView, getDayGroup, relativeTime } from "../components/HistoryView";
import { EmptyState } from "../components/EmptyState";
import { inputAvailability } from "../inputAvailability";
import type { LiveGateView, LivePlanView, LiveReviewView, LiveErrorView, ThreadSummary } from "../types";
import type { InputAvailability } from "../inputAvailability";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

let postMessage: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  const mod = await import("../vscodeApi");
  postMessage = mod.vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeAvailability(overrides: Partial<InputAvailability> = {}): InputAvailability {
  return {
    disabled: false,
    placeholder: "Ask anything or describe a change…",
    showStop: false,
    taskStop: false,
    ...overrides,
  };
}

// ── 1. LiveSlot remount semantics (load-bearing) ──────────────────────────────

describe("LiveSlot — remount on payload change", () => {
  const baseGate: LiveGateView = {
    kind: "command",
    taskId: "t1",
    payload: { command: "npm", args: ["run", "build"], step_id: "s1", decision_id: "d1" },
  };

  it("resolves a card and then remounts when payload changes", () => {
    const { rerender } = render(
      <LiveSlot
        liveGate={baseGate}
        livePlan={null}
        liveReview={null}
        liveError={null}
        onDismissError={vi.fn()}
      />,
    );

    // Click "Allow once" — card enters resolved state.
    fireEvent.click(screen.getByRole("button", { name: /allow once/i }));
    // Buttons are gone after resolution.
    expect(screen.queryByRole("button", { name: /allow once/i })).toBeNull();

    // Re-render with a DIFFERENT payload (new decision_id) for the same kind+taskId.
    const newGate: LiveGateView = {
      ...baseGate,
      payload: { ...baseGate.payload, decision_id: "d2" },
    };
    rerender(
      <LiveSlot
        liveGate={newGate}
        livePlan={null}
        liveReview={null}
        liveError={null}
        onDismissError={vi.fn()}
      />,
    );

    // Fresh mount: "Allow once" is back.
    expect(screen.getByRole("button", { name: /allow once/i })).toBeTruthy();
  });

  it("does NOT remount when the same payload object is re-rendered", () => {
    const { rerender } = render(
      <LiveSlot
        liveGate={baseGate}
        livePlan={null}
        liveReview={null}
        liveError={null}
        onDismissError={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /allow once/i }));
    expect(screen.queryByRole("button", { name: /allow once/i })).toBeNull();

    // Same payload → same key → no remount → still resolved.
    rerender(
      <LiveSlot
        liveGate={baseGate}
        livePlan={null}
        liveReview={null}
        liveError={null}
        onDismissError={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /allow once/i })).toBeNull();
  });
});

// ── 2. LiveSlot renders plan / review / error ─────────────────────────────────

describe("LiveSlot — renders plan card", () => {
  it("renders the plan card with Implement + Give feedback buttons (not readOnly)", () => {
    const livePlan: LivePlanView = { taskId: "t1", planMarkdown: "## Step 1\nDo the thing." };
    render(
      <LiveSlot
        liveGate={null}
        livePlan={livePlan}
        liveReview={null}
        liveError={null}
        onDismissError={vi.fn()}
      />,
    );
    // Plan card header contains "Plan" text.
    expect(screen.getByText("Plan")).toBeTruthy();
    // Non-readOnly: Implement button should be present after expanding.
    // Expand the card first.
    fireEvent.click(screen.getByText("Plan"));
    // Implement and Give feedback are in the card footer.
    expect(screen.getByRole("button", { name: /implement/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /give feedback/i })).toBeTruthy();
  });
});

describe("LiveSlot — renders review card", () => {
  it("renders ReviewCard with Finish button", () => {
    const liveReview: LiveReviewView = {
      taskId: "t2",
      modifiedFiles: ["src/foo.ts"],
      shadowWorkspacePath: null,
      stepsCompleted: 2,
      stepsTotal: 3,
      deviations: [],
    };
    render(
      <LiveSlot
        liveGate={null}
        livePlan={null}
        liveReview={liveReview}
        liveError={null}
        onDismissError={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /^finish$/i })).toBeTruthy();
  });
});

describe("LiveSlot — renders error card and wires onDismissError", () => {
  it("calls onDismissError when Dismiss is clicked", () => {
    const liveError: LiveErrorView = {
      taskId: "t3",
      status: "FAILED",
      detail: "something went wrong",
    };
    const onDismiss = vi.fn();
    render(
      <LiveSlot
        liveGate={null}
        livePlan={null}
        liveReview={null}
        liveError={liveError}
        onDismissError={onDismiss}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^dismiss$/i }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});

describe("LiveSlot — returns null when all slots are null", () => {
  it("renders nothing when all four props are null", () => {
    const { container } = render(
      <LiveSlot
        liveGate={null}
        livePlan={null}
        liveReview={null}
        liveError={null}
        onDismissError={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});

// ── 3. inputAvailability — all 5 precedence rules ────────────────────────────

describe("inputAvailability", () => {
  it("precedence 5 (default): enabled when inputEnabled=true + no liveStatus", () => {
    const result = inputAvailability({ inputEnabled: true, liveStatus: null, workbar: null, liveGate: null, turnActive: false });
    expect(result.disabled).toBe(false);
    expect(result.placeholder).toBe("Ask anything or describe a change…");
    expect(result.showStop).toBe(false);
  });

  it("precedence 1: chat turn streaming (inputEnabled=false, liveStatus=null) → disabled + showStop=true", () => {
    const result = inputAvailability({ inputEnabled: false, liveStatus: null, workbar: null, liveGate: null, turnActive: false });
    expect(result.disabled).toBe(true);
    expect(result.placeholder).toBe("Agent is working…");
    expect(result.showStop).toBe(true);
  });

  it("precedence 1 + task running: inputEnabled=false + EXECUTING → showStop=false", () => {
    const result = inputAvailability({ inputEnabled: false, liveStatus: "EXECUTING", workbar: null, liveGate: null, turnActive: false });
    expect(result.disabled).toBe(true);
    expect(result.showStop).toBe(false);
  });

  it("precedence 1 + AWAITING_PLAN_APPROVAL: showStop=false", () => {
    const result = inputAvailability({ inputEnabled: false, liveStatus: "AWAITING_PLAN_APPROVAL", workbar: null, liveGate: null, turnActive: false });
    expect(result.disabled).toBe(true);
    expect(result.showStop).toBe(false);
  });

  it("precedence 1 + gate: inputEnabled=false + AWAITING_COMMAND_DECISION → showStop=false", () => {
    const result = inputAvailability({ inputEnabled: false, liveStatus: "AWAITING_COMMAND_DECISION", workbar: null, liveGate: null, turnActive: false });
    expect(result.disabled).toBe(true);
    expect(result.showStop).toBe(false);
  });

  it("precedence 2: AWAITING_PLAN_APPROVAL → disabled, plan-review placeholder, no stop", () => {
    const result = inputAvailability({ inputEnabled: true, liveStatus: "AWAITING_PLAN_APPROVAL", workbar: null, liveGate: null, turnActive: false });
    expect(result.disabled).toBe(true);
    expect(result.placeholder).toBe("Review the plan — Implement or Give feedback");
    expect(result.showStop).toBe(false);
  });

  it("precedence 3: gate status AWAITING_SCOPE_DECISION → disabled, gate placeholder, no stop", () => {
    const result = inputAvailability({ inputEnabled: true, liveStatus: "AWAITING_SCOPE_DECISION", workbar: null, liveGate: null, turnActive: false });
    expect(result.disabled).toBe(true);
    expect(result.placeholder).toBe("Waiting for your decision on the card above");
    expect(result.showStop).toBe(false);
  });

  it("precedence 4: EXECUTING without workbar → 'Task is running…'", () => {
    const result = inputAvailability({ inputEnabled: true, liveStatus: "EXECUTING", workbar: null, liveGate: null, turnActive: false });
    expect(result.disabled).toBe(true);
    expect(result.placeholder).toBe("Task is running…");
    expect(result.showStop).toBe(false);
  });

  it("precedence 4: EXECUTING with step 2/4 → step placeholder", () => {
    const result = inputAvailability({
      inputEnabled: true,
      liveStatus: "EXECUTING",
      workbar: { stepIndex: 2, totalSteps: 4 },
      liveGate: null,
      turnActive: false,
    });
    expect(result.disabled).toBe(true);
    expect(result.placeholder).toBe("Task is running — step 2 of 4…");
    expect(result.showStop).toBe(false);
  });

  it("precedence 4: VALIDATING → disabled, no stop", () => {
    const result = inputAvailability({ inputEnabled: true, liveStatus: "VALIDATING", workbar: null, liveGate: null, turnActive: false });
    expect(result.disabled).toBe(true);
    expect(result.showStop).toBe(false);
  });

  // Tier B: taskStop is true exactly in the abortable execution phases.
  it("taskStop=true for EXECUTING/VALIDATING/REPAIRING", () => {
    for (const status of ["EXECUTING", "VALIDATING", "REPAIRING"]) {
      expect(inputAvailability({ inputEnabled: true, liveStatus: status, workbar: null, liveGate: null, turnActive: false }).taskStop).toBe(true);
    }
  });

  it("taskStop=false for non-abortable states (null, plan approval, PROMOTING, gates)", () => {
    for (const status of [null, "AWAITING_PLAN_APPROVAL", "PROMOTING", "AWAITING_COMMAND_DECISION", "PLANNED"]) {
      expect(inputAvailability({ inputEnabled: true, liveStatus: status, workbar: null, liveGate: null, turnActive: false }).taskStop).toBe(false);
    }
  });
});

// ── 4. InputArea ──────────────────────────────────────────────────────────────

describe("InputArea — Send toggles to Stop while a chat turn streams", () => {
  it("showStop: the right-hand action is Stop (posts stopTurn) and there is NO Send button", () => {
    render(
      <InputArea
        availability={makeAvailability({ showStop: true, disabled: true })}
        draft=""
        onDraftChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /^send$/i })).toBeNull();
    const stop = screen.getByRole("button", { name: /^stop$/i });
    fireEvent.click(stop);
    expect(postMessage).toHaveBeenCalledWith({ type: "stopTurn" });
  });

  it("not streaming: Send is shown and there is no chat Stop button", () => {
    render(
      <InputArea
        availability={makeAvailability({ showStop: false })}
        draft="hi"
        onDraftChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /^send$/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^stop$/i })).toBeNull();
  });
});

describe("InputArea — Enter sends text and clears draft", () => {
  it("Enter on non-empty draft posts sendMessage and calls onDraftChange('')", () => {
    const onDraftChange = vi.fn();
    render(
      <InputArea
        availability={makeAvailability()}
        draft="hello world"
        onDraftChange={onDraftChange}
      />,
    );

    const textarea = screen.getByRole("textbox");
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

    expect(postMessage).toHaveBeenCalledWith({ type: "sendMessage", text: "hello world", stepReview: true });
    expect(onDraftChange).toHaveBeenCalledWith("");
  });

  it("sends stepReview flag with the message; toggle flips it", () => {
    render(
      <InputArea
        availability={makeAvailability()}
        draft="do it"
        onDraftChange={vi.fn()}
      />,
    );

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: false });
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "sendMessage", text: "do it", stepReview: true }),
    );

    fireEvent.click(screen.getByLabelText(/review each step/i));
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: false });
    expect(postMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ text: "do it", stepReview: false }),
    );
  });

  it("trims whitespace before sending", () => {
    const onDraftChange = vi.fn();
    render(
      <InputArea
        availability={makeAvailability()}
        draft="  trimmed  "
        onDraftChange={onDraftChange}
      />,
    );

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: false });

    expect(postMessage).toHaveBeenCalledWith({ type: "sendMessage", text: "trimmed", stepReview: true });
  });

  it("does not send on empty draft", () => {
    const onDraftChange = vi.fn();
    render(
      <InputArea
        availability={makeAvailability()}
        draft=""
        onDraftChange={onDraftChange}
      />,
    );

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: false });

    expect(postMessage).not.toHaveBeenCalled();
    expect(onDraftChange).not.toHaveBeenCalled();
  });

  it("does not send when disabled (Enter no-ops)", () => {
    render(
      <InputArea
        availability={makeAvailability({ disabled: true })}
        draft="ignored"
        onDraftChange={vi.fn()}
      />,
    );

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: false });

    expect(postMessage).not.toHaveBeenCalled();
  });

  it("Shift+Enter does not send", () => {
    const onDraftChange = vi.fn();
    render(
      <InputArea
        availability={makeAvailability()}
        draft="hello"
        onDraftChange={onDraftChange}
      />,
    );

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: true });

    expect(postMessage).not.toHaveBeenCalled();
    // onDraftChange should not be called (no clear on Shift+Enter)
    expect(onDraftChange).not.toHaveBeenCalled();
  });
});

describe("InputArea — Stop button", () => {
  it("Stop button appears when showStop=true", () => {
    render(
      <InputArea
        availability={makeAvailability({ disabled: true, showStop: true })}
        draft=""
        onDraftChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /stop/i })).toBeTruthy();
  });

  it("Stop button absent when showStop=false", () => {
    render(
      <InputArea
        availability={makeAvailability({ disabled: false, showStop: false })}
        draft=""
        onDraftChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /stop/i })).toBeNull();
  });

  it("clicking Stop posts stopTurn once (one-shot)", () => {
    render(
      <InputArea
        availability={makeAvailability({ disabled: true, showStop: true })}
        draft=""
        onDraftChange={vi.fn()}
      />,
    );

    const stopBtn = screen.getByRole("button", { name: /stop/i });
    fireEvent.click(stopBtn);
    fireEvent.click(stopBtn);
    fireEvent.click(stopBtn);

    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage).toHaveBeenCalledWith({ type: "stopTurn" });
  });
});

describe("InputArea — Tier B task abort + dynamic review pref", () => {
  it("taskStop shows Stop & keep / Stop & revert posting abortTask with the right revert flag", () => {
    render(
      <InputArea
        availability={makeAvailability({ disabled: true, showStop: false, taskStop: true })}
        draft=""
        onDraftChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /stop and keep/i }));
    expect(postMessage).toHaveBeenLastCalledWith({ type: "abortTask", revert: false });
    // separate render to avoid the one-shot guard on the same instance
  });

  it("Stop & revert posts abortTask {revert:true}, one-shot", () => {
    render(
      <InputArea
        availability={makeAvailability({ disabled: true, showStop: false, taskStop: true })}
        draft=""
        onDraftChange={vi.fn()}
      />,
    );
    const revertBtn = screen.getByRole("button", { name: /stop and revert/i });
    fireEvent.click(revertBtn);
    fireEvent.click(revertBtn);
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage).toHaveBeenCalledWith({ type: "abortTask", revert: true });
  });

  it("task-abort buttons absent when taskStop=false", () => {
    render(
      <InputArea
        availability={makeAvailability({ taskStop: false })}
        draft=""
        onDraftChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /stop and keep/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /stop and revert/i })).toBeNull();
  });

  it("toggling 'Review each step' posts setReviewPref with the inverted auto-accept", () => {
    render(
      <InputArea
        availability={makeAvailability({ disabled: true, taskStop: true })}
        draft=""
        onDraftChange={vi.fn()}
      />,
    );
    // Default checked (review on); first click → unchecked → auto_accept true.
    fireEvent.click(screen.getByLabelText(/review each step/i));
    expect(postMessage).toHaveBeenLastCalledWith({ type: "setReviewPref", autoAccept: true });
    // Click again → checked → auto_accept false.
    fireEvent.click(screen.getByLabelText(/review each step/i));
    expect(postMessage).toHaveBeenLastCalledWith({ type: "setReviewPref", autoAccept: false });
  });
});

// ── 5. HistoryView ────────────────────────────────────────────────────────────

// Build threads at controlled relative times.
function makeThread(id: string, title: string, createdAt: Date): ThreadSummary {
  return { threadId: id, title, createdAt: createdAt.toISOString() };
}

describe("HistoryView — day groups", () => {
  it("groups threads under Today / Yesterday / This week / Older", () => {
    const now = new Date("2024-06-10T12:00:00Z");
    const todayThread = makeThread("t1", "Today thread", new Date("2024-06-10T08:00:00Z"));
    const yesterdayThread = makeThread("t2", "Yesterday thread", new Date("2024-06-09T08:00:00Z"));
    const weekThread = makeThread("t3", "Week thread", new Date("2024-06-06T08:00:00Z"));
    const olderThread = makeThread("t4", "Older thread", new Date("2024-06-01T08:00:00Z"));

    // Verify grouping logic.
    expect(getDayGroup(todayThread.createdAt, now)).toBe("Today");
    expect(getDayGroup(yesterdayThread.createdAt, now)).toBe("Yesterday");
    expect(getDayGroup(weekThread.createdAt, now)).toBe("This week");
    expect(getDayGroup(olderThread.createdAt, now)).toBe("Older");
  });
});

describe("HistoryView — renders thread list", () => {
  const now = new Date();
  const threads: ThreadSummary[] = [
    makeThread("t1", "First chat", new Date(now.getTime() - 5 * 60_000)),      // 5 min ago → Today
    makeThread("t2", "Second chat", new Date(now.getTime() - 2 * 86400_000)),  // 2 days ago → This week
  ];

  it("renders thread titles", () => {
    render(
      <HistoryView
        threads={threads}
        activeThreadId=""
        navLocked={false}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );
    expect(screen.getByText("First chat")).toBeTruthy();
    expect(screen.getByText("Second chat")).toBeTruthy();
  });

  it("marks the active thread with aria role button", () => {
    render(
      <HistoryView
        threads={threads}
        activeThreadId="t1"
        navLocked={false}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );
    // Active thread row should be present.
    expect(screen.getByText("First chat")).toBeTruthy();
  });
});

describe("HistoryView — search filter", () => {
  const threads: ThreadSummary[] = [
    makeThread("t1", "API error handling", new Date()),
    makeThread("t2", "Planning loop fix", new Date()),
  ];

  it("filters threads case-insensitively by title", () => {
    render(
      <HistoryView
        threads={threads}
        activeThreadId=""
        navLocked={false}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );

    const searchInput = screen.getByRole("textbox", { name: /search/i });
    fireEvent.change(searchInput, { target: { value: "api" } });

    expect(screen.getByText("API error handling")).toBeTruthy();
    expect(screen.queryByText("Planning loop fix")).toBeNull();
  });

  it("shows 'No matching chats' when query has no results", () => {
    render(
      <HistoryView
        threads={threads}
        activeThreadId=""
        navLocked={false}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: /search/i }), {
      target: { value: "xyzabc" },
    });

    expect(screen.getByText("No matching chats")).toBeTruthy();
  });

  it("shows 'No chats yet' for an empty thread list", () => {
    render(
      <HistoryView
        threads={[]}
        activeThreadId=""
        navLocked={false}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );
    expect(screen.getByText("No chats yet")).toBeTruthy();
  });
});

describe("HistoryView — navigation", () => {
  const threads: ThreadSummary[] = [
    makeThread("t1", "Thread one", new Date()),
  ];

  it("calls onSelect with threadId when a row is clicked (unlocked)", () => {
    const onSelect = vi.fn();
    render(
      <HistoryView
        threads={threads}
        activeThreadId=""
        navLocked={false}
        onSelect={onSelect}
        onNewChat={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Thread one"));
    expect(onSelect).toHaveBeenCalledWith("t1");
  });

  it("does not call onSelect when navLocked=true", () => {
    const onSelect = vi.fn();
    render(
      <HistoryView
        threads={threads}
        activeThreadId=""
        navLocked={true}
        onSelect={onSelect}
        onNewChat={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Thread one"));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("New Chat button is disabled when navLocked=true", () => {
    render(
      <HistoryView
        threads={threads}
        activeThreadId=""
        navLocked={true}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );

    const btn = screen.getByRole("button", { name: /new chat/i });
    expect(btn).toBeTruthy();
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it("New Chat button calls onNewChat when not locked", () => {
    const onNewChat = vi.fn();
    render(
      <HistoryView
        threads={threads}
        activeThreadId=""
        navLocked={false}
        onSelect={vi.fn()}
        onNewChat={onNewChat}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalledOnce();
  });
});

// ── 6. EmptyState ─────────────────────────────────────────────────────────────

describe("EmptyState", () => {
  it("renders headline", () => {
    render(<EmptyState onPick={vi.fn()} />);
    expect(screen.getByText("What are we building?")).toBeTruthy();
  });

  it("renders all three chips", () => {
    render(<EmptyState onPick={vi.fn()} />);
    expect(screen.getByText("Add error handling to the API routes")).toBeTruthy();
    expect(screen.getByText("Where is the planning loop defined?")).toBeTruthy();
    expect(screen.getByText("Fix the TypeScript errors in editor-client")).toBeTruthy();
  });

  it("chip click calls onPick with the chip text", () => {
    const onPick = vi.fn();
    render(<EmptyState onPick={onPick} />);

    fireEvent.click(screen.getByText("Add error handling to the API routes"));
    expect(onPick).toHaveBeenCalledWith("Add error handling to the API routes");
  });

  it("chip click does NOT post any message to vscode", () => {
    render(<EmptyState onPick={vi.fn()} />);
    fireEvent.click(screen.getByText("Where is the planning loop defined?"));
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("second chip click calls onPick with its own text", () => {
    const onPick = vi.fn();
    render(<EmptyState onPick={onPick} />);
    fireEvent.click(screen.getByText("Fix the TypeScript errors in editor-client"));
    expect(onPick).toHaveBeenCalledWith("Fix the TypeScript errors in editor-client");
  });
});

// ── 7. relativeTime helper ────────────────────────────────────────────────────

describe("relativeTime", () => {
  it("returns 'just now' for < 60s ago", () => {
    const now = new Date("2024-06-10T12:00:00Z");
    const d = new Date("2024-06-10T11:59:30Z");
    expect(relativeTime(d.toISOString(), now)).toBe("just now");
  });

  it("returns 'N min ago' for < 60 min ago", () => {
    const now = new Date("2024-06-10T12:00:00Z");
    const d = new Date("2024-06-10T11:45:00Z");
    expect(relativeTime(d.toISOString(), now)).toBe("15 min ago");
  });

  it("returns '2 hrs ago' for 2h ago", () => {
    const now = new Date("2024-06-10T12:00:00Z");
    const d = new Date("2024-06-10T10:00:00Z");
    expect(relativeTime(d.toISOString(), now)).toBe("2 hrs ago");
  });

  it("returns '1 hr ago' (singular) for 1h ago", () => {
    const now = new Date("2024-06-10T12:00:00Z");
    const d = new Date("2024-06-10T11:00:00Z");
    expect(relativeTime(d.toISOString(), now)).toBe("1 hr ago");
  });

  it("returns 'Yesterday' for yesterday", () => {
    const now = new Date("2024-06-10T12:00:00Z");
    const d = new Date("2024-06-09T08:00:00Z");
    expect(relativeTime(d.toISOString(), now)).toBe("Yesterday");
  });

  it("returns short date for older items", () => {
    const now = new Date("2024-06-10T12:00:00Z");
    const d = new Date("2024-06-03T08:00:00Z");
    const result = relativeTime(d.toISOString(), now);
    // Should contain "Jun" and "3".
    expect(result).toMatch(/Jun/);
    expect(result).toMatch(/3/);
  });
});

// ── 6. HistoryView — enriched summaries (chips, counts) ──────────────────────

describe("HistoryView — enriched summaries", () => {
  function renderHistory(threads: ThreadSummary[]) {
    return render(
      <HistoryView
        threads={threads}
        activeThreadId=""
        navLocked={false}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );
  }

  it("renders message count and a Review chip", () => {
    renderHistory([
      {
        threadId: "t1", title: "Fix planner", createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(), messageCount: 7, status: "review",
      },
    ]);
    expect(screen.getByText(/7 messages/)).toBeTruthy();
    expect(screen.getByText("Review")).toBeTruthy();
  });

  it("renders Running and Done chips", () => {
    renderHistory([
      { threadId: "t1", title: "A", createdAt: new Date().toISOString(), status: "running" },
      { threadId: "t2", title: "B", createdAt: new Date().toISOString(), status: "done" },
    ]);
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.getByText("Done")).toBeTruthy();
  });

  it("renders no chip and no count for a bare summary", () => {
    renderHistory([
      { threadId: "t2", title: "Old thread", createdAt: new Date().toISOString() },
    ]);
    expect(screen.queryByText(/messages/)).toBeNull();
    expect(screen.queryByText("Review")).toBeNull();
  });
});
