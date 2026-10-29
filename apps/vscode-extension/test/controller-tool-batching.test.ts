import { expect, test, vi } from "vitest";
import { CrucibleController } from "../src/controller.js";

// Exercise the actual event router without depending on an SSE transport or a model.
function router() {
  const ui = { appendToolEvent: vi.fn(), appendToolResult: vi.fn() };
  const instance = Object.assign(Object.create(CrucibleController.prototype), {
    ui, openToolEvent: {}, toolEventSeq: 0,
  }) as {
    forwardToolCall(source: string, payload: Record<string, unknown>): void;
    forwardToolResult(source: string, payload: Record<string, unknown>): void;
  };
  return { instance, ui };
}

test("concurrent tool results attach to their own pills in either completion order", () => {
  const { instance, ui } = router();
  instance.forwardToolCall("execution", { call_index: 0, tool: "run_command", args: {} });
  instance.forwardToolCall("execution", { call_index: 1, tool: "web_fetch", args: {} });
  instance.forwardToolResult("execution", { call_index: 0, output: "command failed", is_error: true });
  instance.forwardToolResult("execution", { call_index: 1, output: "page content" });
  expect(ui.appendToolResult.mock.calls).toEqual([
    [0, "command failed", true], [1, "page content", false],
  ]);
});

test("an Auto approval note rides along with its tool result", () => {
  const { instance, ui } = router();
  instance.forwardToolCall("execution", { call_index: 0, tool: "run_command", args: {} });
  instance.forwardToolResult("execution", { call_index: 0, output: "ok", approval_note: "Auto: you asked" });
  expect(ui.appendToolResult.mock.calls).toEqual([[0, "ok", false, "Auto: you asked"]]);
});

test("legacy results still pair by source and orphan results are ignored", () => {
  const { instance, ui } = router();
  instance.forwardToolCall("explore", { tool: "read_file", args: {} });
  instance.forwardToolResult("explore", { output: "source" });
  instance.forwardToolResult("explore", { output: "orphan" });
  expect(ui.appendToolResult.mock.calls).toEqual([[1, "source", false]]);
});
