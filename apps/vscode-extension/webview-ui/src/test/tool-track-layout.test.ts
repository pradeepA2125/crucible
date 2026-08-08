import { describe, it, expect } from "vitest";
import {
  packRows,
  buildTrackPath,
  metricsForWidth,
  NARROW_TRACK_WIDTH,
  TURN_INSET,
  type RowGeometry,
} from "../components/shared/tool-track-layout";

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

describe("metricsForWidth", () => {
  it("returns the wide tier at and above the threshold", () => {
    expect(metricsForWidth(NARROW_TRACK_WIDTH)).toEqual({ sidePad: 28, colGap: 14, turnInset: 9 });
    expect(metricsForWidth(NARROW_TRACK_WIDTH + 200)).toEqual({
      sidePad: 28,
      colGap: 14,
      turnInset: 9,
    });
  });

  it("returns the narrow tier below the threshold", () => {
    expect(metricsForWidth(NARROW_TRACK_WIDTH - 1)).toEqual({ sidePad: 8, colGap: 8, turnInset: 4 });
    expect(metricsForWidth(0)).toEqual({ sidePad: 8, colGap: 8, turnInset: 4 });
  });

  it("keeps turnInset strictly below sidePad in both tiers", () => {
    // The turn axis sits at width - turnInset and a row's content edge sits
    // at width - sidePad. If turnInset ever caught up to (or passed) sidePad,
    // the vertical run would land inside the content lane instead of beyond
    // it — this is the one invariant a future tweak to either tier must not
    // break, so it is pinned for both, not just the narrow one that motivated it.
    const wide = metricsForWidth(NARROW_TRACK_WIDTH);
    const narrow = metricsForWidth(NARROW_TRACK_WIDTH - 1);
    expect(wide.turnInset).toBeLessThan(wide.sidePad);
    expect(narrow.turnInset).toBeLessThan(narrow.sidePad);
  });
});

describe("buildTrackPath", () => {
  it("draws nothing for no rows", () => {
    const path = buildTrackPath([], 300, TURN_INSET);
    expect(path.d).toBe("");
    expect(path.startCap).toBeNull();
    expect(path.endCap).toBeNull();
  });

  it("draws a bare rail with both caps for a single row", () => {
    const rows: RowGeometry[] = [{ y: 10, startX: 28, endX: 200, dirRight: true }];
    const path = buildTrackPath(rows, 300, TURN_INSET);
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
    const path = buildTrackPath(rows, 300, TURN_INSET);
    // Turn axis is width - TURN_INSET = 291 (the wide tier's inset).
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
    const path = buildTrackPath(rows, 300, TURN_INSET);
    // Second turn runs down the left axis at TURN_INSET = 9.
    expect(path.d).toContain("Q 9 44 9 56");
    expect(path.d).toContain("Q 9 78 21 78");
    expect(path.d.endsWith("L 158 78")).toBe(true);
    expect(path.endCap).toEqual({ cx: 158, cy: 78 });
  });

  it("honours a narrow inset: the turn axis sits at width - 4, not width - 9", () => {
    const rows: RowGeometry[] = [
      { y: 10, startX: 28, endX: 272, dirRight: true },
      { y: 44, startX: 272, endX: 28, dirRight: false },
    ];
    const path = buildTrackPath(rows, 300, 4);
    expect(path.d).toBe(
      "M 20 10 L 284 10 Q 296 10 296 22 L 296 32 Q 296 44 284 44 L 20 44"
    );
    expect(path.endCap).toEqual({ cx: 20, cy: 44 });
  });
});
