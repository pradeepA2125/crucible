import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ErrorCard } from "../components/messages/ErrorCard";
import { ReviewCard } from "../components/messages/ReviewCard";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

let postMessage: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  const mod = await import("../vscodeApi");
  postMessage = mod.vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

// ── 6. ErrorCard ──────────────────────────────────────────────────────────────

describe("ErrorCard — renders FAILED title", () => {
  it("shows 'Execution failed' for FAILED status", () => {
    render(
      <ErrorCard
        taskId="task-err"
        status="FAILED"
        onDismiss={() => {}}
      />
    );
    expect(screen.getByText("Execution failed")).toBeTruthy();
  });

  it("shows 'Task aborted' for ABORTED status", () => {
    render(
      <ErrorCard
        taskId="task-err"
        status="ABORTED"
        onDismiss={() => {}}
      />
    );
    expect(screen.getByText("Task aborted")).toBeTruthy();
  });
});

describe("ErrorCard — Resume", () => {
  it("posts resumeTask with stage:execute", () => {
    render(
      <ErrorCard taskId="task-fail" status="FAILED" onDismiss={() => {}} />
    );

    fireEvent.click(screen.getByRole("button", { name: /resume/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "resumeTask",
      taskId: "task-fail",
      stage: "execute",
    });
  });
});

describe("ErrorCard — Re-plan", () => {
  it("posts resumeTask with stage:plan", () => {
    render(
      <ErrorCard taskId="task-fail" status="FAILED" onDismiss={() => {}} />
    );

    fireEvent.click(screen.getByRole("button", { name: /re-plan/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "resumeTask",
      taskId: "task-fail",
      stage: "plan",
    });
  });
});

describe("ErrorCard — Dismiss", () => {
  it("calls onDismiss and posts NOTHING", () => {
    const onDismiss = vi.fn();
    render(
      <ErrorCard taskId="task-fail" status="FAILED" onDismiss={onDismiss} />
    );

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    // onDismiss callback was called
    expect(onDismiss).toHaveBeenCalledTimes(1);
    // No message should have been posted to the extension
    expect(postMessage).not.toHaveBeenCalled();
  });
});

describe("ErrorCard — one-shot after Resume", () => {
  it("action buttons gone after Resume; resolved row shown", () => {
    render(
      <ErrorCard taskId="task-fail" status="FAILED" onDismiss={() => {}} />
    );

    fireEvent.click(screen.getByRole("button", { name: /resume/i }));

    expect(screen.queryByRole("button", { name: /resume/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /re-plan/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /dismiss/i })).toBeNull();
    expect(screen.getByText("Resuming…")).toBeTruthy();
  });
});

// ── 7. ReviewCard ─────────────────────────────────────────────────────────────

const REVIEW_PROPS = {
  taskId: "task-review",
  modifiedFiles: ["src/index.ts", "src/utils.ts"],
  shadowWorkspacePath: "/tmp/shadow",
  stepsCompleted: 3,
  stepsTotal: 4,
  deviations: ["Revised step 2 due to compile error"],
};

describe("ReviewCard — renders", () => {
  it("renders steps line", () => {
    render(<ReviewCard {...REVIEW_PROPS} />);
    expect(screen.getByText("3 of 4 steps completed")).toBeTruthy();
  });

  it("renders deviations", () => {
    render(<ReviewCard {...REVIEW_PROPS} />);
    expect(screen.getByText("Revised step 2 due to compile error")).toBeTruthy();
  });

  it("renders modified file basenames", () => {
    render(<ReviewCard {...REVIEW_PROPS} />);
    expect(screen.getByText("index.ts")).toBeTruthy();
    expect(screen.getByText("utils.ts")).toBeTruthy();
  });
});

describe("ReviewCard — Finish", () => {
  it("posts acceptTask and shows resolved row", () => {
    render(<ReviewCard {...REVIEW_PROPS} />);

    // Use exact match to avoid matching "Close without finishing"
    fireEvent.click(screen.getByRole("button", { name: /^finish$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "acceptTask",
      taskId: "task-review",
    });

    // Resolved label shown
    expect(screen.getByText("Finishing…")).toBeTruthy();
    // Buttons gone
    expect(screen.queryByRole("button", { name: /^finish$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /close without finishing/i })).toBeNull();
  });
});

describe("ReviewCard — Close flow posts rejectTask with typed reason", () => {
  it("Close without finishing reveals reason input; Close posts rejectTask", () => {
    render(<ReviewCard {...REVIEW_PROPS} />);

    fireEvent.click(screen.getByRole("button", { name: /close without finishing/i }));

    // Reason input appears
    const input = screen.getByPlaceholderText(/reason/i);
    expect(input).toBeTruthy();

    // Type a reason
    fireEvent.change(input, { target: { value: "not needed" } });

    // Click Close (the danger button in the close row)
    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "rejectTask",
      taskId: "task-review",
      reason: "not needed",
    });

    // Resolved row shown
    expect(screen.getByText("Closed")).toBeTruthy();
  });

  it("empty reason sends 'closed from chat' default", () => {
    render(<ReviewCard {...REVIEW_PROPS} />);

    fireEvent.click(screen.getByRole("button", { name: /close without finishing/i }));
    // Do not type anything — leave reason empty
    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));

    expect(postMessage).toHaveBeenCalledWith({
      type: "rejectTask",
      taskId: "task-review",
      reason: "closed from chat",
    });
  });

  it("Cancel returns to idle", () => {
    render(<ReviewCard {...REVIEW_PROPS} />);

    fireEvent.click(screen.getByRole("button", { name: /close without finishing/i }));

    // Cancel the close
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    // Back to idle: Finish and Close without finishing visible again
    expect(screen.getByRole("button", { name: /^finish$/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /close without finishing/i })).toBeTruthy();

    // Nothing was posted
    expect(postMessage).not.toHaveBeenCalled();
  });
});

describe("ReviewCard — one-shot throughout", () => {
  it("after Finish all action buttons are gone and double-click is safe", () => {
    render(<ReviewCard {...REVIEW_PROPS} />);

    const finishBtn = screen.getByRole("button", { name: /^finish$/i });
    fireEvent.click(finishBtn);

    // Button is gone — second click cannot fire
    expect(screen.queryByRole("button", { name: /finish/i })).toBeNull();
    // postMessage called exactly once
    expect(postMessage).toHaveBeenCalledTimes(1);
  });
});

// ── 8. Regression: existing 41 tests still pass (covered by test suite runner) ──
// No explicit test needed here — the test runner reports all files together.
