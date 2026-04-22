import { describe, expect, test, vi } from "vitest";
import type { ReviewPanelViewModel } from "../src/types.js";

describe("renderPanelHtml", () => {
  test("renders markdown plan review state and feedback controls", async () => {
    vi.mock("vscode", () => ({
      window: { createWebviewPanel: vi.fn() },
      ViewColumn: { One: 1 },
    }));
    const { renderPanelHtml } = await import("../src/review-panel.js");

    const model: ReviewPanelViewModel = {
      session: {
        taskId: "task-1",
        status: "AWAITING_PLAN_APPROVAL",
        workspacePath: "/tmp/workspace",
        backendBaseUrl: "http://127.0.0.1:8000",
        updatedAt: "2026-03-03T00:00:00.000Z",
      },
      task: {
        taskId: "task-1",
        goal: "Add endpoint",
        status: "AWAITING_PLAN_APPROVAL",
        modifiedFiles: [],
        diagnostics: [],
        planMarkdown: "# Plan\n\n- Add route",
      },
      result: null,
      reviewFiles: [],
    };

    const html = renderPanelHtml(model);

    expect(html).toContain("Engineering Plan (Proposed)");
    expect(html).toContain("# Plan");
    expect(html).toContain("Comments / Corrections:");
    expect(html).toContain("Approve / Regenerate Plan");
  });
});
