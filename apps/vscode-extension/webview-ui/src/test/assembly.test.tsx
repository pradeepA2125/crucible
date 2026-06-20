import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MessageRow } from "../components/MessageRow";
import { ThreadView } from "../components/ThreadView";
import App from "../App";
import type { ChatMsg, AppState, ThreadSummary } from "../types";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

let postMessage: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  const mod = await import("../vscodeApi");
  postMessage = mod.vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeState(overrides: Partial<AppState> = {}): AppState {
  return {
    view: "thread",
    threads: [{ threadId: "t1", title: "Test Thread", createdAt: new Date().toISOString() }],
    activeThreadId: "t1",
    messages: [],
    streaming: null,
    thinkingStatus: null,
    inputEnabled: true,
    liveGate: null,
    livePlan: null,
    liveReview: null,
    liveError: null,
    workbar: null,
    liveStatus: null,
    turnActive: false,
    ...overrides,
  };
}

function makeMsg(overrides: Partial<ChatMsg> = {}): ChatMsg {
  return {
    role: "agent",
    content: "",
    type: "text",
    timestamp: new Date().toISOString(),
    metadata: {},
    ...overrides,
  };
}

// ── 1. MessageRow: plan_card renders PlanCard read-only ───────────────────────

describe("MessageRow — plan_card", () => {
  it("renders PlanCard with no Implement button (readOnly)", () => {
    const msg = makeMsg({
      type: "plan_card",
      role: "agent",
      content: "## Step 1\nDo the thing.",
      metadata: { taskId: "task-1" },
    });

    render(<MessageRow msg={msg} planVersion={1} />);

    // PlanCard renders its header ("Plan" text)
    expect(screen.getByText("Plan")).toBeTruthy();

    // No Implement button — readOnly mode
    expect(screen.queryByRole("button", { name: /implement/i })).toBeNull();
  });

  it("second plan_card for the same task gets version badge v2", () => {
    const thread = makeState({
      messages: [
        makeMsg({
          type: "plan_card",
          role: "agent",
          content: "## Step 1\nFirst version.",
          metadata: { taskId: "task-42" },
        }),
        makeMsg({
          type: "plan_card",
          role: "agent",
          content: "## Step 1\nRevised version.",
          metadata: { taskId: "task-42" },
        }),
      ],
    });

    render(
      <ThreadView
        state={thread}
        onBack={vi.fn()}
        dismissedErrorTaskId={null}
        onDismissError={vi.fn()}
      />,
    );

    // v2 badge should appear for the second plan card.
    expect(screen.getByText("v2")).toBeTruthy();
  });
});

// ── 2. MessageRow: legacy command_card renders read-only summary ──────────────

describe("MessageRow — legacy command_card", () => {
  it("renders a read-only summary with no decision buttons", () => {
    const msg = makeMsg({
      type: "command_card",
      role: "agent",
      content: "",
      metadata: { command: "npm run build" },
    });

    render(<MessageRow msg={msg} />);

    // Title should be visible.
    expect(screen.getByText("Command approval (resolved)")).toBeTruthy();

    // No Allow once / Reject buttons.
    expect(screen.queryByRole("button", { name: /allow once/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /reject/i })).toBeNull();
  });
});

// ── 3. MessageRow: breadcrumb → AgentRow; user text → UserMessage ─────────────

describe("MessageRow — breadcrumb and user messages", () => {
  it("breadcrumb metadata → AgentRow path (leading marker stripped)", () => {
    const msg = makeMsg({
      type: "text",
      role: "agent",
      content: "✓ Plan approved",
      metadata: { breadcrumb: true },
    });

    render(<MessageRow msg={msg} />);

    // BreadcrumbLine strips the ✓ and shows the rest.
    expect(screen.getByText("Plan approved")).toBeTruthy();
    // The literal "✓" should not appear as a text node (it's replaced by an icon).
    const el = screen.getByText("Plan approved");
    expect(el.textContent).not.toContain("✓");
  });

  it("user message → UserMessage (self-end bubble)", () => {
    const msg = makeMsg({
      type: "text",
      role: "user",
      content: "Hello world",
    });

    const { container } = render(<MessageRow msg={msg} />);

    expect(screen.getByText("Hello world")).toBeTruthy();
    // UserMessage renders a self-end bubble with a border-radius style.
    const bubble = container.firstChild as HTMLElement;
    expect(bubble?.className).toMatch(/self-end/);
  });
});

// ── 4. ThreadView: empty state ────────────────────────────────────────────────

describe("ThreadView — empty state", () => {
  it("shows EmptyState when messages is empty and no streaming", () => {
    const state = makeState({ messages: [], streaming: null, thinkingStatus: null });

    render(
      <ThreadView
        state={state}
        onBack={vi.fn()}
        dismissedErrorTaskId={null}
        onDismissError={vi.fn()}
      />,
    );

    expect(screen.getByText("What are we building?")).toBeTruthy();
  });

  it("chip click pre-fills the textarea (assert textarea value)", () => {
    const state = makeState({ messages: [], streaming: null, thinkingStatus: null });

    render(
      <ThreadView
        state={state}
        onBack={vi.fn()}
        dismissedErrorTaskId={null}
        onDismissError={vi.fn()}
      />,
    );

    const chip = screen.getByText("Add error handling to the API routes");
    fireEvent.click(chip);

    const textarea = screen.getByRole("textbox", { name: /chat input/i });
    expect((textarea as HTMLTextAreaElement).value).toBe(
      "Add error handling to the API routes",
    );

    // Clicking a chip must NOT send a message.
    expect(postMessage).not.toHaveBeenCalled();
  });
});

// ── 5. ThreadView: WorkBar visibility ─────────────────────────────────────────

describe("ThreadView — WorkBar", () => {
  it("shows WorkBar with status-map label when liveStatus=EXECUTING and inputEnabled=false", () => {
    const state = makeState({
      inputEnabled: false,
      liveStatus: "EXECUTING",
    });

    render(
      <ThreadView
        state={state}
        onBack={vi.fn()}
        dismissedErrorTaskId={null}
        onDismissError={vi.fn()}
      />,
    );

    expect(screen.getByText("Executing…")).toBeTruthy();
  });

  it("shows step label when workbar has stepIndex + totalSteps + stepTitle", () => {
    const state = makeState({
      inputEnabled: false,
      liveStatus: "EXECUTING",
      workbar: { stepIndex: 2, totalSteps: 4, stepTitle: "Wire handler" },
    });

    render(
      <ThreadView
        state={state}
        onBack={vi.fn()}
        dismissedErrorTaskId={null}
        onDismissError={vi.fn()}
      />,
    );

    expect(screen.getByText("Step 2 of 4")).toBeTruthy();
    // Step title is in a sibling span — use regex to handle normalization.
    expect(screen.getByText(/Wire handler/)).toBeTruthy();
  });

  it("hides WorkBar when liveStatus=AWAITING_PLAN_APPROVAL", () => {
    const state = makeState({
      inputEnabled: false,
      liveStatus: "AWAITING_PLAN_APPROVAL",
    });

    render(
      <ThreadView
        state={state}
        onBack={vi.fn()}
        dismissedErrorTaskId={null}
        onDismissError={vi.fn()}
      />,
    );

    // "Executing..." and "Working…" should not appear.
    expect(screen.queryByText("Executing…")).toBeNull();
    expect(screen.queryByText("Working…")).toBeNull();
    // The animated spinner (no aria label, so check for "0:00" timer absence).
    expect(screen.queryByText("0:00")).toBeNull();
  });
});

// ── 6. ThreadView: navLocked disables back + new-chat buttons ─────────────────

describe("ThreadView — navLocked", () => {
  it("back and new-chat buttons are disabled when navLocked", () => {
    const state = makeState({ inputEnabled: false, liveStatus: null });

    render(
      <ThreadView
        state={state}
        onBack={vi.fn()}
        dismissedErrorTaskId={null}
        onDismissError={vi.fn()}
      />,
    );

    const backBtn = screen.getByRole("button", { name: /back to history/i });
    const newChatBtn = screen.getByRole("button", { name: /new chat/i });

    expect((backBtn as HTMLButtonElement).disabled).toBe(true);
    expect((newChatBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it("back button calls onBack when not locked", () => {
    const onBack = vi.fn();
    const state = makeState({ inputEnabled: true });

    render(
      <ThreadView
        state={state}
        onBack={onBack}
        dismissedErrorTaskId={null}
        onDismissError={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /back to history/i }));
    expect(onBack).toHaveBeenCalledOnce();
  });
});

// ── 7. App: renders HistoryView or ThreadView ─────────────────────────────────

describe("App — view routing", () => {
  it("renders HistoryView when no activeThreadId", () => {
    // We drive App through window messages, seeding a renderThreadList with empty activeThreadId.
    render(<App />);

    // Dispatch renderThreadList with empty activeThreadId.
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: {
            type: "renderThreadList",
            threads: [
              {
                threadId: "t1",
                title: "Hello thread",
                createdAt: new Date().toISOString(),
              },
            ] as ThreadSummary[],
            activeThreadId: "",
          },
        }),
      );
    });

    // HistoryView shows "AI Editor" title.
    expect(screen.getByText("AI Editor")).toBeTruthy();
    // And our thread title.
    expect(screen.getByText("Hello thread")).toBeTruthy();
  });

  it("switches to ThreadView when activeThreadId is set and view=thread", () => {
    render(<App />);

    // First: supply a thread list with an active thread.
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: {
            type: "renderThreadList",
            threads: [
              {
                threadId: "t1",
                title: "Hello thread",
                createdAt: new Date().toISOString(),
              },
            ] as ThreadSummary[],
            activeThreadId: "t1",
          },
        }),
      );
    });

    // App view defaults to "history" on initial load; click the thread row to navigate.
    const threadRow = screen.getByText("Hello thread");
    fireEvent.click(threadRow);

    // After navigation, ThreadView renders with the thread title in the header.
    // ThreadView header shows the thread title.
    expect(screen.queryByText("AI Editor")).toBeNull(); // HistoryView header gone
    // "Hello thread" now appears as the ThreadView header title.
    expect(screen.getByText("Hello thread")).toBeTruthy();
  });
});
