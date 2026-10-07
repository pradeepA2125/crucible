import { describe, expect, it } from "vitest";
import { cachedPercent, threadBreakdown, usageText } from "./usage";

const u = (requests: number, input: number, output: number, cached = 0) =>
  ({ requests, input, output, cached });

describe("usage", () => {
  it("formats one line with the cached share", () => {
    expect(usageText(u(38, 412_000, 9_100, 292_520))).toBe("↑412k ↓9.1k · 38 req · 71% cached");
    expect(usageText(u(1, 900, 20))).toBe("↑900 ↓20 · 1 req");
    expect(cachedPercent(u(0, 0, 0))).toBe(0);
  });

  it("breaks a thread down into main, teams and the other agents", () => {
    const rows = threadBreakdown({
      total: u(10, 10_000, 100), main: u(2, 2_000, 20),
      agents: { a: u(3, 3_000, 30), b: u(4, 4_000, 40), c: u(1, 1_000, 10) },
      teams: { t1: { total: u(7, 7_000, 70), members: {} } },
    }, { t1: "tetris" });
    expect(rows.map((r) => [r.label, r.usage.requests])).toEqual([
      ["Main agent", 2], ["Team tetris", 7], ["Other agents", 1]]);
  });

  it("leaves out rows with no requests", () => {
    const rows = threadBreakdown(
      { total: u(1, 10, 1), main: u(1, 10, 1), agents: {}, teams: {} }, {});
    expect(rows.map((r) => r.label)).toEqual(["Main agent"]);
  });
});
