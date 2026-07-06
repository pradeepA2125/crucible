// Pure scene math — kept WebGL-free so it runs in jsdom tests.
import type { EdgeKind } from "./types";

export function starSize(inDeg: number, outDeg: number, isHub: boolean): number {
  return 1.6 + Math.sqrt(inDeg + outDeg) * 0.62 + (isHub ? 2.2 : 0);
}

export function particleCount(edgeCount: number): number {
  return Math.min(48, Math.round(6 + 14 * Math.log10(Math.max(10, edgeCount))));
}

const KINDS: EdgeKind[] = ["Imports", "Calls", "Inherits", "References"];

/** Largest-remainder allocation of `total` particles across a kind mix. */
export function mixToCounts(
  kindMix: Partial<Record<EdgeKind, number>>,
  total: number
): Record<EdgeKind, number> {
  const sum = KINDS.reduce((acc, k) => acc + (kindMix[k] ?? 0), 0);
  const out = { Imports: 0, Calls: 0, Inherits: 0, References: 0 } as Record<EdgeKind, number>;
  if (sum === 0) {
    out.Imports = total;
    return out;
  }
  const exact = KINDS.map((k) => ((kindMix[k] ?? 0) / sum) * total);
  let used = 0;
  exact.forEach((e, i) => {
    out[KINDS[i]!] += Math.floor(e);
    used += Math.floor(e);
  });
  const remainders = exact.map((e, i) => ({ i, r: e - Math.floor(e) })).sort((a, b) => b.r - a.r);
  for (let j = 0; j < total - used; j++) out[KINDS[remainders[j % 4]!.i]!] += 1;
  return out;
}

export function sphericalToPosition(
  yaw: number,
  pitch: number,
  radius: number,
  target: [number, number, number]
): [number, number, number] {
  return [
    target[0] + radius * Math.cos(pitch) * Math.sin(yaw),
    target[1] + radius * Math.sin(pitch),
    target[2] + radius * Math.cos(pitch) * Math.cos(yaw),
  ];
}
