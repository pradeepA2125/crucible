import { describe, expect, test } from "vitest";
import { canTransition, createTaskRecord, transitionTask } from "../src/domain/task-state.js";

describe("task state machine", () => {
  test("allows valid transition sequence", () => {
    const task = createTaskRecord({
      taskId: "t1",
      goal: "goal",
      budget: {
        maxIterations: 3,
        maxFilesTouched: 3,
        maxTokens: 1000,
        maxRuntimeMs: 10_000
      }
    });

    const t1 = transitionTask(task, "CONTEXT_READY", "context ready");
    const t2 = transitionTask(transitionTask(t1, "AWAITING_PLAN_APPROVAL", "plan"), "PLANNED", "planned");
    expect(t2.status).toBe("PLANNED");
    expect(t2.events).toHaveLength(3);
    expect(canTransition("PLANNED", "EXECUTING")).toBe(true);
  });

  test("supports review and promotion transitions", () => {
    const task = createTaskRecord({
      taskId: "t-review",
      goal: "goal",
      budget: {
        maxIterations: 3,
        maxFilesTouched: 3,
        maxTokens: 1000,
        maxRuntimeMs: 10_000
      }
    });

    const contextReady = transitionTask(task, "CONTEXT_READY", "context");
    const awaitingApproval = transitionTask(contextReady, "AWAITING_PLAN_APPROVAL", "awaiting");
    const planned = transitionTask(awaitingApproval, "PLANNED", "planned");
    const executing = transitionTask(planned, "EXECUTING", "executing");
    const validating = transitionTask(executing, "VALIDATING", "validating");
    const validated = transitionTask(validating, "VALIDATED", "validated");
    const review = transitionTask(validated, "READY_FOR_REVIEW", "ready");
    const promoting = transitionTask(review, "PROMOTING", "promoting");
    const succeeded = transitionTask(promoting, "SUCCEEDED", "done");

    expect(succeeded.status).toBe("SUCCEEDED");
  });

  test("rejects invalid transition", () => {
    const task = createTaskRecord({
      taskId: "t2",
      goal: "goal",
      budget: {
        maxIterations: 3,
        maxFilesTouched: 3,
        maxTokens: 1000,
        maxRuntimeMs: 10_000
      }
    });

    expect(() => transitionTask(task, "PLANNED", "invalid")).toThrow(
      "Invalid transition"
    );
  });
});
