import { describe, expect, it } from "vitest";
import { DISPATCH_HOLD_MS, commitFloorMs } from "./turnTiming";

describe("commit floor", () => {
  it.each([
    [[], 700],
    [[500, 600], 700],
    [[1000, 2000, 3000], 2750],
    [[5000, 5000, 5000], 3000],
    [[300, 350, 390, 100], 700],
    [[300, 1000, 2000, 3000], 2750],
  ])("%j resumed pauses give a %i ms floor", (pauses, floor) => {
    expect(commitFloorMs(pauses)).toBe(floor);
  });
  it("holds tasks and corrections for two seconds", () => {
    expect(DISPATCH_HOLD_MS).toBe(2000);
  });
});
