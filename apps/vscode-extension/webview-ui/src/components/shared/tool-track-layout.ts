/**
 * Pure geometry for the serpentine tool track.
 *
 * Deliberately free of React and the DOM: jsdom reports zero for every layout
 * metric, so this module is the only place the track's behaviour can actually
 * be asserted. The component measures; this module decides.
 */

/** Horizontal lane reserved on each side for the U-turns, as row padding. */
export const SIDE_PAD = 28;
/** Gap between pills within a row. */
export const COL_GAP = 14;
/** Vertical gap between rows. */
export const ROW_GAP = 24;
/** Corner radius of a U-turn. */
export const TURN_RADIUS = 12;
/** Distance from the container edge to the U-turn's vertical run. */
export const TURN_INSET = 9;
/** How far the rail runs past the first and last pill of a row. */
export const RAIL_OVERSHOOT = 8;
/** Radius of the start ring and end dot. */
export const CAP_RADIUS = 3.5;

/**
 * Side padding, column gap, and turn inset scale together with the measured
 * track width — everything else (row gap, turn radius, overshoot, cap
 * radius) stays fixed across both tiers.
 */
export interface TrackMetrics {
  sidePad: number;
  colGap: number;
  turnInset: number;
}

/** Wide tier: exactly the approved wireframe's values, unchanged. */
const WIDE_METRICS: TrackMetrics = { sidePad: SIDE_PAD, colGap: COL_GAP, turnInset: TURN_INSET };

/**
 * Narrow tier, for a docked sidebar. `turnInset` shrinks in lockstep with
 * `sidePad` rather than independently: the turn axis sits at
 * `width - turnInset` and a row's content edge sits at `width - sidePad`.
 * Leaving the inset at the wide tier's 9 while the pad drops to 8 would put
 * the vertical run *inside* the content lane, so the road would cut through
 * the last pill instead of turning beyond it.
 */
const NARROW_METRICS: TrackMetrics = { sidePad: 8, colGap: 8, turnInset: 4 };

/**
 * Track width at and above which the wide tier applies. Below it, `SIDE_PAD`
 * alone claims more width than a docked ~300px sidebar has to give — less
 * than two finished pills plus a gap — and the track degrades to one pill
 * per row, taller than the flat wall this feature replaces.
 */
export const NARROW_TRACK_WIDTH = 420;

/** Choose the geometry tier from the measured track width. */
export function metricsForWidth(trackWidth: number): TrackMetrics {
  return trackWidth >= NARROW_TRACK_WIDTH ? WIDE_METRICS : NARROW_METRICS;
}

/**
 * Greedy-pack pill widths into rows that fit `avail`.
 *
 * Returns indices into `widths`, grouped by row, in call order. A pill wider
 * than `avail` gets a row to itself and overflows within it — the same thing
 * flex-wrap does today, rather than being dropped or shrunk.
 */
export function packRows(widths: number[], avail: number, gap: number): number[][] {
  const rows: number[][] = [];
  let current: number[] = [];
  let used = 0;

  for (let i = 0; i < widths.length; i++) {
    const cost = current.length === 0 ? widths[i] : gap + widths[i];
    if (current.length > 0 && used + cost > avail) {
      rows.push(current);
      current = [];
      used = 0;
    }
    used += current.length === 0 ? widths[i] : gap + widths[i];
    current.push(i);
  }

  if (current.length > 0) rows.push(current);
  return rows;
}

/** One rendered row, measured. `startX`/`endX` are the road's entry and exit. */
export interface RowGeometry {
  y: number;
  /** x of the row's earliest call, on the side the road enters. */
  startX: number;
  /** x of the row's latest call, on the side the road leaves. */
  endX: number;
  dirRight: boolean;
}

export interface TrackCap {
  cx: number;
  cy: number;
}

export interface TrackPath {
  d: string;
  startCap: TrackCap | null;
  endCap: TrackCap | null;
}

/**
 * Build the serpentine's SVG path.
 *
 * Even rows run left-to-right and turn on the right; odd rows run right-to-left
 * and turn on the left, so the call after a row's last pill is the next row's
 * first pill directly beneath it. The final row never runs on to an edge — it
 * stops at its last pill, where the caller draws the end dot.
 *
 * `turnInset` is required, not defaulted: it must come from the same
 * `TrackMetrics` tier as the row padding that produced `rows`, and a default
 * would let a caller silently mix a narrow layout with the wide inset.
 */
export function buildTrackPath(rows: RowGeometry[], width: number, turnInset: number): TrackPath {
  if (rows.length === 0) return { d: "", startCap: null, endCap: null };

  const axisRight = width - turnInset;
  const axisLeft = turnInset;
  const lead = (row: RowGeometry) => (row.dirRight ? -RAIL_OVERSHOOT : RAIL_OVERSHOOT);
  const tail = (row: RowGeometry) => (row.dirRight ? RAIL_OVERSHOOT : -RAIL_OVERSHOOT);

  let d = `M ${rows[0].startX + lead(rows[0])} ${rows[0].y}`;

  rows.forEach((row, index) => {
    if (index === rows.length - 1) {
      d += ` L ${row.endX + tail(row)} ${row.y}`;
      return;
    }
    const axis = row.dirRight ? axisRight : axisLeft;
    // Approach direction: a rightward row meets its turn from the left.
    const approach = row.dirRight ? -1 : 1;
    const nextY = rows[index + 1].y;
    d +=
      ` L ${axis + approach * TURN_RADIUS} ${row.y}` +
      ` Q ${axis} ${row.y} ${axis} ${row.y + TURN_RADIUS}` +
      ` L ${axis} ${nextY - TURN_RADIUS}` +
      ` Q ${axis} ${nextY} ${axis + approach * TURN_RADIUS} ${nextY}`;
  });

  const first = rows[0];
  const last = rows[rows.length - 1];
  return {
    d,
    startCap: { cx: first.startX + lead(first), cy: first.y },
    endCap: { cx: last.endX + tail(last), cy: last.y },
  };
}
