// Deterministic space layout: package ring anchors -> seeded directory cores ->
// seeded gaussian star scatter -> bounded intra-package spring refinement.
// Every random draw is keyed by a stable id hash, so the same model always
// produces the same positions and snapshot refreshes morph instead of reshuffle.
import type { LayoutResult, SpaceModel } from "./types";

export function hash32(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussOf(rng: () => number): number {
  return (rng() + rng() + rng() - 1.5) / 1.5;
}

const FORCE_ITERATIONS = 60;
// Rest length must sit well BELOW the typical in-cluster pair distance (~30+ even in
// small packages), or the spring pushes coupled files APART instead of together.
const SPRING_REST = 14;
const SPRING_K = 0.02;

export function computeLayout(model: SpaceModel): LayoutResult {
  const ids = model.stars.map((s) => s.id);
  const index = new Map(ids.map((id, i) => [id, i]));
  const positions = new Float32Array(ids.length * 3);

  // Package ring anchors — angle slot from the package id hash (stable regardless of
  // which other packages exist), radius from total scale.
  const totalFiles = model.stars.length || 1;
  const ringRadius = 420 * Math.max(1, Math.sqrt(totalFiles / 300));
  const anchors = new Map<string, [number, number, number]>();
  for (const p of model.packages) {
    const rng = mulberry32(hash32("pkg:" + p.id));
    const angle = rng() * Math.PI * 2;
    anchors.set(p.id, [Math.cos(angle) * ringRadius, (rng() - 0.5) * 160, Math.sin(angle) * ringRadius]);
  }

  for (const s of model.stars) {
    const i = index.get(s.id)! * 3;
    if (!s.pkg) {
      const rng = mulberry32(hash32("orphan:" + s.id));
      positions[i] = gaussOf(rng) * ringRadius * 0.35;
      positions[i + 1] = gaussOf(rng) * ringRadius * 0.2;
      positions[i + 2] = gaussOf(rng) * ringRadius * 0.35;
      continue;
    }
    const [ax, ay, az] = anchors.get(s.pkg) ?? [0, 0, 0];
    const fileCount = model.packages.find((p) => p.id === s.pkg)?.fileCount ?? 10;
    const spread = Math.min(260, 60 + fileCount * 0.85);
    const dirRng = mulberry32(hash32("dir:" + s.pkg + ":" + s.dir));
    const cx = ax + gaussOf(dirRng) * spread * 0.8;
    const cy = ay + gaussOf(dirRng) * spread * 0.3;
    const cz = az + gaussOf(dirRng) * spread * 0.8;
    const rng = mulberry32(hash32("star:" + s.id));
    positions[i] = cx + gaussOf(rng) * spread * 0.55;
    positions[i + 1] = cy + gaussOf(rng) * spread * 0.22;
    positions[i + 2] = cz + gaussOf(rng) * spread * 0.55;
  }

  // Spring refinement over intra-package links (deterministic iteration order).
  const springs = model.links
    .map((l) => ({ a: index.get(l.a), b: index.get(l.b), w: Math.min(4, Math.sqrt(l.count)) }))
    .filter((s): s is { a: number; b: number; w: number } => s.a !== undefined && s.b !== undefined);
  for (let iter = 0; iter < FORCE_ITERATIONS; iter++) {
    for (const sp of springs) {
      const ia = sp.a * 3;
      const ib = sp.b * 3;
      const dx = positions[ib]! - positions[ia]!;
      const dy = positions[ib + 1]! - positions[ia + 1]!;
      const dz = positions[ib + 2]! - positions[ia + 2]!;
      const dist = Math.hypot(dx, dy, dz) || 1;
      const f = (SPRING_K * sp.w * (dist - SPRING_REST)) / dist / 2;
      positions[ia]! += dx * f;
      positions[ia + 1]! += dy * f;
      positions[ia + 2]! += dz * f;
      positions[ib]! -= dx * f;
      positions[ib + 1]! -= dy * f;
      positions[ib + 2]! -= dz * f;
    }
  }

  return { ids, positions };
}
