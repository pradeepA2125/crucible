// Instanced star sprites + package nebula billboards + background dust.
// One THREE.Points for all stars: per-vertex size/color/flags/phase attributes,
// shader-driven twinkle, beacon pulse, hub halo, and per-star dim factor.
import * as THREE from "three";
import type { Palette } from "../palette";
import { starSize } from "../scene-math";
import { hash32, mulberry32 } from "../layout";
import type { LayoutResult, StarRecord } from "../types";

const STAR_VERT = /* glsl */ `
  attribute float aSize;
  attribute vec3 aColor;
  attribute float aFlags;   // bit 0 = entry beacon, bit 1 = hub
  attribute float aPhase;
  attribute float aDim;
  uniform float uTime;
  varying vec3 vColor;
  varying float vFlags;
  varying float vPulse;
  varying float vDim;
  void main() {
    vColor = aColor;
    vFlags = aFlags;
    vDim = aDim;
    float tw = 0.82 + 0.18 * sin(uTime * 1.3 + aPhase);
    vPulse = fract(uTime * 0.55 + aPhase);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * tw * (900.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;
const STAR_FRAG = /* glsl */ `
  varying vec3 vColor;
  varying float vFlags;
  varying float vPulse;
  varying float vDim;
  void main() {
    vec2 uv = gl_PointCoord - 0.5;
    float d = length(uv) * 2.0;
    if (d > 1.0) discard;
    float core = smoothstep(0.5, 0.0, d);
    float halo = smoothstep(1.0, 0.25, d) * 0.55;
    vec3 col = vColor * (core * 1.6 + halo);
    float isEntry = step(0.5, mod(vFlags, 2.0));
    // expanding beacon ring
    float ring = isEntry * smoothstep(0.06, 0.0, abs(d - vPulse)) * (1.0 - vPulse);
    col += vec3(0.99, 0.87, 0.28) * ring;
    float isHub = step(1.5, vFlags);
    col += vColor * isHub * smoothstep(0.05, 0.0, abs(d - 0.82)) * 0.5;
    gl_FragColor = vec4(col * vDim, (core + halo + ring) * vDim);
  }
`;

interface PosTween {
  i: number;
  from: [number, number, number];
  to: [number, number, number];
  start: number;
}
interface Birth {
  i: number;
  target: number;
  start: number;
}
interface PendingMorph {
  stars: StarRecord[];
  layout: LayoutResult;
  pkgOrder: string[];
  applyAt: number;
}

const MORPH_TWEEN_SEC = 0.9;
const IGNITE_SEC = 0.6;
const FADE_SEC = 0.4;

/** Pure centroid computation — usable before the geometry rebuild lands. */
export function computeCentroids(
  stars: StarRecord[],
  layout: LayoutResult
): Map<string, [number, number, number]> {
  const idx = new Map(layout.ids.map((id, i) => [id, i]));
  const acc = new Map<string, { x: number; y: number; z: number; n: number }>();
  for (const s of stars) {
    if (!s.pkg) continue;
    const i = idx.get(s.id);
    if (i === undefined) continue;
    const c = acc.get(s.pkg) ?? { x: 0, y: 0, z: 0, n: 0 };
    c.x += layout.positions[i * 3]!;
    c.y += layout.positions[i * 3 + 1]!;
    c.z += layout.positions[i * 3 + 2]!;
    c.n += 1;
    acc.set(s.pkg, c);
  }
  const out = new Map<string, [number, number, number]>();
  for (const [pkg, c] of acc) if (c.n) out.set(pkg, [c.x / c.n, c.y / c.n, c.z / c.n]);
  return out;
}

export class Starfield {
  readonly points: THREE.Points;
  private geo = new THREE.BufferGeometry();
  private mat: THREE.ShaderMaterial;
  private ids: string[] = [];
  private idIndex = new Map<string, number>();
  private stars: StarRecord[] = [];
  private nebulae: THREE.Sprite[] = [];
  private dust: THREE.Points | null = null;
  private pkgIndex = new Map<string, number>();
  private posTweens: PosTween[] = [];
  private births: Birth[] = [];
  private fading: number[] = [];
  private pendingMorph: PendingMorph | null = null;

  constructor(
    private readonly scene: THREE.Scene,
    private readonly pal: Palette
  ) {
    this.mat = new THREE.ShaderMaterial({
      vertexShader: STAR_VERT,
      fragmentShader: STAR_FRAG,
      uniforms: { uTime: { value: 0 } },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    this.points = new THREE.Points(this.geo, this.mat);
    this.points.frustumCulled = false;
    scene.add(this.points);
    this.addDust();
  }

  setStars(stars: StarRecord[], layout: LayoutResult, pkgOrder: string[]): void {
    this.stars = stars;
    this.ids = layout.ids;
    this.idIndex = new Map(layout.ids.map((id, i) => [id, i]));
    this.pkgIndex = new Map(pkgOrder.map((p, i) => [p, i]));
    const n = layout.ids.length;
    const sizes = new Float32Array(n);
    const colors = new Float32Array(n * 3);
    const flags = new Float32Array(n);
    const phases = new Float32Array(n);
    const dims = new Float32Array(n).fill(1);
    const byId = new Map(stars.map((s) => [s.id, s]));
    const c = new THREE.Color();
    layout.ids.forEach((id, i) => {
      const s = byId.get(id);
      if (!s) return;
      sizes[i] = starSize(s.inDeg, s.outDeg, s.isHub) * 3.4;
      const tint = s.isEntry
        ? this.pal.beacon
        : s.pkg
          ? this.pal.clusterTints[(this.pkgIndex.get(s.pkg) ?? 0) % this.pal.clusterTints.length]!
          : this.pal.star;
      c.set(tint);
      colors.set([c.r, c.g, c.b], i * 3);
      flags[i] = (s.isEntry ? 1 : 0) + (s.isHub ? 2 : 0);
      phases[i] = (hash32(id) % 6283) / 1000;
    });
    this.geo.setAttribute("position", new THREE.BufferAttribute(layout.positions.slice(), 3));
    this.geo.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
    this.geo.setAttribute("aColor", new THREE.BufferAttribute(colors, 3));
    this.geo.setAttribute("aFlags", new THREE.BufferAttribute(flags, 1));
    this.geo.setAttribute("aPhase", new THREE.BufferAttribute(phases, 1));
    this.geo.setAttribute("aDim", new THREE.BufferAttribute(dims, 1));
    this.geo.computeBoundingSphere();
    this.rebuildNebulae();
  }

  /** Per-package nebula: additive radial-gradient sprite at the package centroid. */
  private rebuildNebulae(): void {
    for (const n of this.nebulae) this.scene.remove(n);
    this.nebulae = [];
    const pos = this.geo.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!pos) return;
    const centroids = new Map<string, { x: number; y: number; z: number; n: number }>();
    for (const s of this.stars) {
      if (!s.pkg) continue;
      const i = this.idIndex.get(s.id);
      if (i === undefined) continue;
      const c = centroids.get(s.pkg) ?? { x: 0, y: 0, z: 0, n: 0 };
      c.x += pos.getX(i);
      c.y += pos.getY(i);
      c.z += pos.getZ(i);
      c.n += 1;
      centroids.set(s.pkg, c);
    }
    for (const [pkg, c] of centroids) {
      if (!c.n) continue;
      const tint = this.pal.clusterTints[(this.pkgIndex.get(pkg) ?? 0) % this.pal.clusterTints.length]!;
      const sprite = new THREE.Sprite(
        new THREE.SpriteMaterial({
          map: nebulaTexture(tint),
          transparent: true,
          opacity: this.pal.nebulaAlpha * 2.9,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        })
      );
      sprite.position.set(c.x / c.n, c.y / c.n, c.z / c.n);
      const scale = 180 + Math.sqrt(c.n) * 60;
      sprite.scale.set(scale, scale, 1);
      sprite.userData.pkg = pkg;
      this.scene.add(sprite);
      this.nebulae.push(sprite);
    }
  }

  private addDust(): void {
    const rng = mulberry32(1337);
    const n = 420;
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const r = 900 + rng() * 2200;
      const th = rng() * Math.PI * 2;
      const ph = (rng() - 0.5) * Math.PI;
      pos[i * 3] = Math.cos(th) * Math.cos(ph) * r;
      pos[i * 3 + 1] = Math.sin(ph) * r * 0.6;
      pos[i * 3 + 2] = Math.sin(th) * Math.cos(ph) * r;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    this.dust = new THREE.Points(
      g,
      new THREE.PointsMaterial({
        color: this.pal.star,
        size: 1.6,
        sizeAttenuation: false,
        transparent: true,
        opacity: 0.28,
        depthWrite: false,
      })
    );
    this.scene.add(this.dust);
  }

  positionOf(id: string): [number, number, number] | null {
    const i = this.idIndex.get(id);
    if (i === undefined) return null;
    const p = this.geo.getAttribute("position") as THREE.BufferAttribute;
    return [p.getX(i), p.getY(i), p.getZ(i)];
  }

  pkgCentroids(): Map<string, [number, number, number]> {
    const out = new Map<string, [number, number, number]>();
    for (const s of this.nebulae) {
      out.set(s.userData.pkg as string, [s.position.x, s.position.y, s.position.z]);
    }
    return out;
  }

  starAt(index: number): StarRecord | null {
    const id = this.ids[index];
    if (!id) return null;
    return this.stars.find((s) => s.id === id) ?? null;
  }

  starById(id: string): StarRecord | null {
    return this.stars.find((s) => s.id === id) ?? null;
  }

  /** Dim everything except `keep` ids (or a whole kept package). null/null = undim all. */
  setDimSet(keep: Set<string> | null, pkgKeep: string | null): void {
    const dims = this.geo.getAttribute("aDim") as THREE.BufferAttribute | undefined;
    if (!dims) return;
    const byId = new Map(this.stars.map((s) => [s.id, s]));
    this.ids.forEach((id, i) => {
      const s = byId.get(id);
      const kept =
        !keep && !pkgKeep ? true : (keep?.has(id) ?? false) || (pkgKeep !== null && s?.pkg === pkgKeep);
      dims.setX(i, kept ? 1 : 0.18);
    });
    dims.needsUpdate = true;
    for (const n of this.nebulae) {
      const mat = n.material as THREE.SpriteMaterial;
      mat.opacity = !pkgKeep && !keep ? 0.16 : n.userData.pkg === pkgKeep ? 0.22 : 0.06;
    }
  }

  /** Morph the field to a new model: removed stars fade out, retained stars tween
   * to their new positions, added stars ignite from zero size. */
  morphTo(
    stars: StarRecord[],
    layout: LayoutResult,
    pkgOrder: string[],
    removedIds: string[],
    now: number
  ): void {
    const fadeIdx = removedIds.map((id) => this.idIndex.get(id)).filter((i): i is number => i !== undefined);
    this.pendingMorph = {
      stars,
      layout,
      pkgOrder,
      applyAt: fadeIdx.length ? now + FADE_SEC : now,
    };
    this.fading = fadeIdx;
    if (!fadeIdx.length) this.applyPendingMorph(now);
  }

  private applyPendingMorph(now: number): void {
    const pm = this.pendingMorph;
    if (!pm) return;
    this.pendingMorph = null;
    this.fading = [];
    // Capture old positions before the rebuild so retained stars can tween.
    const oldPos = new Map<string, [number, number, number]>();
    const prev = this.geo.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (prev) {
      this.ids.forEach((id, i) => oldPos.set(id, [prev.getX(i), prev.getY(i), prev.getZ(i)]));
    }
    this.setStars(pm.stars, pm.layout, pm.pkgOrder);
    const pos = this.geo.getAttribute("position") as THREE.BufferAttribute;
    const sizes = this.geo.getAttribute("aSize") as THREE.BufferAttribute;
    this.posTweens = [];
    this.births = [];
    pm.layout.ids.forEach((id, i) => {
      const from = oldPos.get(id);
      const to: [number, number, number] = [pos.getX(i), pos.getY(i), pos.getZ(i)];
      if (from) {
        pos.setXYZ(i, from[0], from[1], from[2]);
        this.posTweens.push({ i, from, to, start: now });
      } else {
        this.births.push({ i, target: sizes.getX(i), start: now });
        sizes.setX(i, 0);
      }
    });
    pos.needsUpdate = true;
    sizes.needsUpdate = true;
  }

  update(t: number): void {
    this.mat.uniforms.uTime!.value = t;
    if (this.fading.length) {
      const dims = this.geo.getAttribute("aDim") as THREE.BufferAttribute | undefined;
      const pm = this.pendingMorph;
      if (dims && pm) {
        const k = Math.min(1, Math.max(0, 1 - (pm.applyAt - t) / FADE_SEC));
        for (const i of this.fading) dims.setX(i, 1 - k);
        dims.needsUpdate = true;
      }
    }
    if (this.pendingMorph && t >= this.pendingMorph.applyAt) this.applyPendingMorph(t);
    if (this.posTweens.length) {
      const pos = this.geo.getAttribute("position") as THREE.BufferAttribute;
      this.posTweens = this.posTweens.filter((tw) => {
        const k = Math.min(1, (t - tw.start) / MORPH_TWEEN_SEC);
        const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        pos.setXYZ(
          tw.i,
          tw.from[0] + (tw.to[0] - tw.from[0]) * e,
          tw.from[1] + (tw.to[1] - tw.from[1]) * e,
          tw.from[2] + (tw.to[2] - tw.from[2]) * e
        );
        return k < 1;
      });
      pos.needsUpdate = true;
      this.geo.computeBoundingSphere();
    }
    if (this.births.length) {
      const sizes = this.geo.getAttribute("aSize") as THREE.BufferAttribute;
      this.births = this.births.filter((b) => {
        const k = Math.min(1, (t - b.start) / IGNITE_SEC);
        // ease-out-back: overshoot to 1.4x then settle — the "ignite" flare
        const c1 = 1.70158;
        const c3 = c1 + 1;
        const e = 1 + c3 * Math.pow(k - 1, 3) + c1 * Math.pow(k - 1, 2);
        sizes.setX(b.i, b.target * Math.max(0, e));
        return k < 1;
      });
      sizes.needsUpdate = true;
    }
  }

  dispose(): void {
    this.geo.dispose();
    this.mat.dispose();
    for (const n of this.nebulae) (n.material as THREE.SpriteMaterial).dispose();
    if (this.dust) {
      this.dust.geometry.dispose();
      (this.dust.material as THREE.PointsMaterial).dispose();
    }
  }
}

const texCache = new Map<string, THREE.CanvasTexture>();
function nebulaTexture(color: string): THREE.CanvasTexture {
  let t = texCache.get(color);
  if (t) return t;
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, color + "cc");
  grad.addColorStop(0.4, color + "44");
  grad.addColorStop(1, "transparent");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  t = new THREE.CanvasTexture(c);
  texCache.set(color, t);
  return t;
}
