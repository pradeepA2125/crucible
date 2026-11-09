/** How long a finished-sounding turn must stay quiet before the voice agent acts on it. */

export const DISPATCH_HOLD_MS = 2000;
const FLOOR_MS = 700;
const CEILING_MS = 3000;
const MARGIN_MS = 250;
const COUNTED_PAUSE_MS = 400;
const MIN_SAMPLES = 3;

/**
 * Learns from the pauses after which this speaker kept talking: once there are a few,
 * wait a little longer than most of them (the 75th percentile, linearly interpolated).
 */
export function commitFloorMs(resumedPauses: readonly number[]): number {
  const pauses = resumedPauses.filter(pause => pause >= COUNTED_PAUSE_MS).sort((a, b) => a - b);
  if (pauses.length < MIN_SAMPLES) return FLOOR_MS;
  const position = 0.75 * (pauses.length - 1);
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, pauses.length - 1);
  const p75 = pauses[lower] + (pauses[upper] - pauses[lower]) * (position - lower);
  return Math.round(Math.min(CEILING_MS, Math.max(FLOOR_MS, p75 + MARGIN_MS)));
}
