import { describe, it, expect } from "vitest";
import { packRows } from "../components/shared/tool-track-layout";

describe("packRows", () => {
  it("returns no rows for no pills", () => {
    expect(packRows([], 300, 14)).toEqual([]);
  });

  it("keeps pills that fit on one row", () => {
    expect(packRows([40, 40, 40], 300, 14)).toEqual([[0, 1, 2]]);
  });

  it("counts the gap between pills but not before the first", () => {
    // 40 + 14 + 40 = 94 — fits in 94 exactly, does not fit in 93.
    expect(packRows([40, 40], 94, 14)).toEqual([[0, 1]]);
    expect(packRows([40, 40], 93, 14)).toEqual([[0], [1]]);
  });

  it("gives an over-wide pill its own row rather than dropping it", () => {
    expect(packRows([200], 100, 14)).toEqual([[0]]);
  });

  it("does not merge neighbours around an over-wide pill", () => {
    expect(packRows([40, 200, 40], 100, 14)).toEqual([[0], [1], [2]]);
  });

  it("breaks into as many rows as the width demands", () => {
    // Each pill 40 wide, gap 14 => 2 per row at width 100 (40+14+40=94).
    expect(packRows([40, 40, 40, 40, 40], 100, 14)).toEqual([[0, 1], [2, 3], [4]]);
  });
});
