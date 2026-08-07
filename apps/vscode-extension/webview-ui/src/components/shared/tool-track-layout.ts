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
