import { describe, it, expect } from "vitest";
import { packRows, buildTrackPath, type RowGeometry } from "../components/shared/tool-track-layout";

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

describe("buildTrackPath", () => {
  it("draws nothing for no rows", () => {
    const path = buildTrackPath([], 300);
    expect(path.d).toBe("");
    expect(path.startCap).toBeNull();
    expect(path.endCap).toBeNull();
  });

  it("draws a bare rail with both caps for a single row", () => {
    const rows: RowGeometry[] = [{ y: 10, startX: 28, endX: 200, dirRight: true }];
    const path = buildTrackPath(rows, 300);
    // Overshoots 8px before the first pill and 8px past the last.
    expect(path.d).toBe("M 20 10 L 208 10");
    expect(path.startCap).toEqual({ cx: 20, cy: 10 });
    expect(path.endCap).toEqual({ cx: 208, cy: 10 });
  });

  it("turns on the right after a left-to-right row", () => {
    const rows: RowGeometry[] = [
      { y: 10, startX: 28, endX: 272, dirRight: true },
      { y: 44, startX: 272, endX: 28, dirRight: false },
    ];
    const path = buildTrackPath(rows, 300);
    // Turn axis is width - TURN_INSET = 291.
    expect(path.d).toBe(
      "M 20 10 L 279 10 Q 291 10 291 22 L 291 32 Q 291 44 279 44 L 20 44"
    );
    // The road ends at the last row's last pill, not at an edge.
    expect(path.endCap).toEqual({ cx: 20, cy: 44 });
  });

  it("turns on the left after a right-to-left row", () => {
    const rows: RowGeometry[] = [
      { y: 10, startX: 28, endX: 272, dirRight: true },
      { y: 44, startX: 272, endX: 28, dirRight: false },
      { y: 78, startX: 28, endX: 150, dirRight: true },
    ];
    const path = buildTrackPath(rows, 300);
    // Second turn runs down the left axis at TURN_INSET = 9.
    expect(path.d).toContain("Q 9 44 9 56");
    expect(path.d).toContain("Q 9 78 21 78");
    expect(path.d.endsWith("L 158 78")).toBe(true);
    expect(path.endCap).toEqual({ cx: 158, cy: 78 });
  });
});
