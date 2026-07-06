import { describe, expect, it } from "vitest";
import { mixToCounts, particleCount, sphericalToPosition, starSize } from "./scene-math";

describe("scene math", () => {
  it("starSize grows sub-linearly with degree and boosts hubs", () => {
    expect(starSize(0, 0, false)).toBeGreaterThan(0);
    expect(starSize(20, 20, false)).toBeGreaterThan(starSize(2, 2, false));
    expect(starSize(20, 20, false)).toBeLessThan(starSize(2, 2, false) * 5);
    expect(starSize(10, 10, true)).toBeGreaterThan(starSize(10, 10, false));
  });

  it("particleCount is log-scaled and capped at 48", () => {
    expect(particleCount(10)).toBeGreaterThanOrEqual(6);
    expect(particleCount(1840)).toBeGreaterThan(particleCount(100));
    expect(particleCount(1_000_000)).toBeLessThanOrEqual(48);
  });

  it("mixToCounts allocates exactly total via largest remainder", () => {
    const c = mixToCounts({ Imports: 62, Calls: 30, Inherits: 8 }, 20);
    expect(c.Imports + c.Calls + c.Inherits + c.References).toBe(20);
    expect(c.Imports).toBeGreaterThan(c.Calls);
    expect(mixToCounts({}, 10).Imports).toBe(10); // degenerate mix -> all Imports
  });

  it("sphericalToPosition matches hand-computed axes", () => {
    const [x, y, z] = sphericalToPosition(0, 0, 100, [0, 0, 0]);
    expect(x).toBeCloseTo(0);
    expect(y).toBeCloseTo(0);
    expect(z).toBeCloseTo(100);
    const [, y2] = sphericalToPosition(0, Math.PI / 2, 100, [0, 0, 0]);
    expect(y2).toBeCloseTo(100);
  });
});
