// Instanced star sprites + package nebula billboards + background dust.
// One THREE.Points for all stars: per-vertex size/color/flags/phase attributes,
// shader-driven twinkle, beacon pulse, hub halo, and per-star dim factor.
import * as THREE from "three";
import { EMBER } from "../palette";
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

  constructor(private readonly scene: THREE.Scene) {
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
        ? EMBER.beacon
        : s.pkg
          ? EMBER.clusterTints[(this.pkgIndex.get(s.pkg) ?? 0) % EMBER.clusterTints.length]!
          : EMBER.star;
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
      const tint = EMBER.clusterTints[(this.pkgIndex.get(pkg) ?? 0) % EMBER.clusterTints.length]!;
      const sprite = new THREE.Sprite(
        new THREE.SpriteMaterial({
          map: nebulaTexture(tint),
          transparent: true,
          opacity: 0.16,
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
        color: EMBER.star,
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

  update(t: number): void {
    this.mat.uniforms.uTime!.value = t;
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
