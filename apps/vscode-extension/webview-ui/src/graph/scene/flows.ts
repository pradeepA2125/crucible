// Energy beams (bundles), selection trace threads, and L3 symbol satellites.
// Beam/thread particles are GPU-driven: the vertex shader evaluates a quadratic
// bezier from uP0/uP1/uP2 uniforms at t = fract(aT0 + uTime * aSpeed), so a single
// uTime bump animates every stream. Kind-layer toggles are a one-hot dot product
// (dot(uKindOn, aKindMask)) — no rebuilds on toggle.
import * as THREE from "three";
import { EMBER } from "../palette";
import { mixToCounts, particleCount } from "../scene-math";
import { hash32, mulberry32 } from "../layout";
import type { Bundle, EdgeKind, FileDetail, SymbolDetail } from "../types";

export interface SatellitePick {
  symbolId: string;
  line?: number;
}

const KINDS: EdgeKind[] = ["Imports", "Calls", "Inherits", "References"];

const PARTICLE_VERT = /* glsl */ `
  attribute float aT0;
  attribute float aSpeed;
  attribute vec3 aColor;
  attribute vec4 aKindMask;
  uniform vec3 uP0;
  uniform vec3 uP1;
  uniform vec3 uP2;
  uniform float uTime;
  uniform vec4 uKindOn;
  uniform float uSize;
  varying vec3 vColor;
  varying float vOn;
  void main() {
    float t = fract(aT0 + uTime * aSpeed);
    vec3 p01 = mix(uP0, uP1, t);
    vec3 p12 = mix(uP1, uP2, t);
    vec3 pos = mix(p01, p12, t);
    vColor = aColor;
    vOn = dot(uKindOn, aKindMask);
    vec4 mv = modelViewMatrix * vec4(pos, 1.0);
    gl_PointSize = uSize * vOn * (900.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;
const PARTICLE_FRAG = /* glsl */ `
  varying vec3 vColor;
  varying float vOn;
  void main() {
    if (vOn < 0.5) discard;
    vec2 uv = gl_PointCoord - 0.5;
    float d = length(uv) * 2.0;
    if (d > 1.0) discard;
    float core = smoothstep(0.55, 0.0, d);
    float halo = smoothstep(1.0, 0.3, d) * 0.4;
    gl_FragColor = vec4(vColor * (core * 1.5 + halo), core + halo);
  }
`;

interface BeamSystem {
  line: THREE.Line;
  points: THREE.Points;
  mat: THREE.ShaderMaterial;
  lineKind: EdgeKind;
}
interface ThreadSystem {
  line: THREE.Line;
  points: THREE.Points;
  mat: THREE.ShaderMaterial;
  kind: EdgeKind;
  targetId: string;
  tint: string;
}
interface Satellite {
  symbolId: string;
  name: string;
  line?: number;
  r: number;
  tilt: number;
  speed: number;
  phase: number;
}

function bezierUniforms(p0: THREE.Vector3, p1: THREE.Vector3, p2: THREE.Vector3, size: number) {
  return {
    uP0: { value: p0 },
    uP1: { value: p1 },
    uP2: { value: p2 },
    uTime: { value: 0 },
    uKindOn: { value: new THREE.Vector4(1, 1, 1, 0) },
    uSize: { value: size },
  };
}

function sampleCurve(p0: THREE.Vector3, p1: THREE.Vector3, p2: THREE.Vector3): THREE.BufferGeometry {
  const curve = new THREE.QuadraticBezierCurve3(p0, p1, p2);
  return new THREE.BufferGeometry().setFromPoints(curve.getPoints(26));
}

function particleGeometry(
  count: number,
  seedKey: string,
  colorsByKind: Partial<Record<EdgeKind, number>>,
  speedRange: [number, number],
  reverseFraction: number
): THREE.BufferGeometry {
  const rng = mulberry32(hash32(seedKey));
  const geo = new THREE.BufferGeometry();
  // GPU bezier evaluation ignores `position`, but three requires the attribute.
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  const t0 = new Float32Array(count);
  const speed = new Float32Array(count);
  const color = new Float32Array(count * 3);
  const kindMask = new Float32Array(count * 4);
  const alloc = mixToCounts(colorsByKind, count);
  const kindOfParticle: EdgeKind[] = [];
  for (const k of KINDS) for (let i = 0; i < alloc[k]; i++) kindOfParticle.push(k);
  const c = new THREE.Color();
  for (let i = 0; i < count; i++) {
    t0[i] = rng();
    const mag = speedRange[0] + rng() * (speedRange[1] - speedRange[0]);
    speed[i] = rng() < reverseFraction ? -mag : mag;
    const kind = kindOfParticle[i] ?? "Imports";
    c.set(EMBER.kinds[kind]);
    color.set([c.r, c.g, c.b], i * 3);
    kindMask[i * 4 + KINDS.indexOf(kind)] = 1;
  }
  geo.setAttribute("aT0", new THREE.BufferAttribute(t0, 1));
  geo.setAttribute("aSpeed", new THREE.BufferAttribute(speed, 1));
  geo.setAttribute("aColor", new THREE.BufferAttribute(color, 3));
  geo.setAttribute("aKindMask", new THREE.BufferAttribute(kindMask, 4));
  return geo;
}

export class Flows {
  private beams: BeamSystem[] = [];
  private threads: ThreadSystem[] = [];
  private satGroup: THREE.Points | null = null;
  private satMeta: Satellite[] = [];
  private satCenter = new THREE.Vector3();
  private focusedFileId: string | null = null;
  private layers: Record<EdgeKind, boolean> = { Imports: true, Calls: true, Inherits: true, References: false };
  private bundleControls = new Map<string, THREE.Vector3>();

  constructor(private readonly scene: THREE.Scene) {}

  setBundles(bundles: Bundle[], centroids: Map<string, [number, number, number]>): void {
    this.clearBeams();
    for (const bu of bundles) {
      const a = centroids.get(bu.fromPkg);
      const b = centroids.get(bu.toPkg);
      if (!a || !b) continue;
      const p0 = new THREE.Vector3(...a);
      const p2 = new THREE.Vector3(...b);
      const rng = mulberry32(hash32(`bundle:${bu.fromPkg}->${bu.toPkg}`));
      const mid = p0.clone().lerp(p2, 0.5);
      const outward = new THREE.Vector3(mid.x, 0, mid.z);
      const outLen = outward.length() || 1;
      outward.divideScalar(outLen);
      const p1 = mid
        .clone()
        .addScaledVector(outward, 190 * (0.55 + rng() * 0.5))
        .add(new THREE.Vector3(0, 90 + (rng() - 0.5) * 100, 0));
      this.bundleControls.set(`${bu.fromPkg}->${bu.toPkg}`, p1);

      const dominant = (Object.entries(bu.kindMix).sort((x, y) => (y[1] ?? 0) - (x[1] ?? 0))[0]?.[0] ??
        "Imports") as EdgeKind;
      const lineGeo = sampleCurve(p0, p1, p2);
      const line = new THREE.Line(
        lineGeo,
        new THREE.LineBasicMaterial({
          color: EMBER.kinds[dominant],
          transparent: true,
          opacity: 0.1,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        })
      );
      const mat = new THREE.ShaderMaterial({
        vertexShader: PARTICLE_VERT,
        fragmentShader: PARTICLE_FRAG,
        uniforms: bezierUniforms(p0, p1, p2, 9),
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      this.applyLayersTo(mat);
      const points = new THREE.Points(
        particleGeometry(
          particleCount(bu.count),
          `bundleP:${bu.fromPkg}->${bu.toPkg}`,
          bu.kindMix,
          [0.05, 0.125],
          0.32
        ),
        mat
      );
      points.frustumCulled = false;
      line.frustumCulled = false;
      this.scene.add(line, points);
      this.beams.push({ line, points, mat, lineKind: dominant });
    }
  }

  traceFile(detail: FileDetail, posOf: (id: string) => [number, number, number] | null): void {
    this.clearTrace();
    this.focusedFileId = detail.fileId;
    const from = posOf(detail.fileId);
    if (!from) return;
    const p0v = new THREE.Vector3(...from);
    const seen = new Set<string>();
    let n = 0;
    for (const e of detail.edges) {
      if (n >= 40) break;
      if (seen.has(e.otherFile + e.dir + e.kind)) continue;
      seen.add(e.otherFile + e.dir + e.kind);
      const to = posOf(e.otherFile);
      if (!to) continue;
      this.addThread(p0v, new THREE.Vector3(...to), e.kind, e.dir, e.otherFile);
      n += 1;
    }
  }

  traceSymbol(detail: SymbolDetail, posOf: (id: string) => [number, number, number] | null): void {
    this.clearTrace();
    if (!this.satGroup) return;
    const p0v = this.satCenter.clone();
    let n = 0;
    for (const e of detail.edges) {
      if (n >= 24 || !e.fileId) continue;
      const to = posOf(e.fileId);
      if (!to) continue;
      this.addThread(p0v, new THREE.Vector3(...to), e.kind, e.dir, e.fileId);
      n += 1;
    }
  }

  private addThread(
    myPos: THREE.Vector3,
    otherPos: THREE.Vector3,
    kind: EdgeKind,
    dir: "out" | "in",
    targetId: string
  ): void {
    // Direction of particle flow = edge direction: out flows me->other, in flows other->me.
    const p0 = dir === "out" ? myPos.clone() : otherPos.clone();
    const p2 = dir === "out" ? otherPos.clone() : myPos.clone();
    const rng = mulberry32(hash32(`thread:${targetId}:${kind}:${dir}`));
    const mid = p0.clone().lerp(p2, 0.5);
    const p1 = mid.add(
      new THREE.Vector3((rng() - 0.5) * 90, 40 + (rng() - 0.5) * 60, (rng() - 0.5) * 90)
    );
    const tint = dir === "out" ? EMBER.out : EMBER.inn;
    const line = new THREE.Line(
      sampleCurve(p0, p1, p2),
      new THREE.LineBasicMaterial({
        color: tint,
        transparent: true,
        opacity: 0.34,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      })
    );
    const mat = new THREE.ShaderMaterial({
      vertexShader: PARTICLE_VERT,
      fragmentShader: PARTICLE_FRAG,
      uniforms: bezierUniforms(p0, p1, p2, 11),
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    this.applyLayersTo(mat);
    const points = new THREE.Points(
      particleGeometry(3, `threadP:${targetId}:${kind}:${dir}`, { [kind]: 1 }, [0.25, 0.45], 0),
      mat
    );
    // Thread particles carry the direction color, not the kind color.
    const c = new THREE.Color(tint);
    const colAttr = points.geometry.getAttribute("aColor") as THREE.BufferAttribute;
    for (let i = 0; i < colAttr.count; i++) colAttr.setXYZ(i, c.r, c.g, c.b);
    colAttr.needsUpdate = true;
    points.frustumCulled = false;
    line.frustumCulled = false;
    line.visible = points.visible = this.layers[kind];
    this.scene.add(line, points);
    this.threads.push({ line, points, mat, kind, targetId, tint });
  }

  showSatellites(detail: FileDetail, center: [number, number, number]): void {
    this.clearSatellites();
    this.satCenter.set(...center);
    const symbols = detail.symbols.slice(0, 24);
    this.satMeta = symbols.map((s, i) => {
      const rng = mulberry32(hash32(`sat:${s.id}`));
      const meta: Satellite = {
        symbolId: s.id,
        name: s.name,
        r: 16 + i * 4.6 + rng() * 3,
        tilt: (rng() - 0.5) * 1.1,
        speed: (0.35 + rng() * 0.5) * (i % 2 === 0 ? 1 : -1),
        phase: rng() * Math.PI * 2,
      };
      if (s.line) meta.line = s.line;
      return meta;
    });
    const n = this.satMeta.length;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    const colors = new Float32Array(n * 3);
    const kindColor: Record<string, string> = {
      Class: EMBER.kinds.Inherits,
      Function: EMBER.kinds.Calls,
      Method: EMBER.kinds.Imports,
      Interface: EMBER.beacon,
    };
    const c = new THREE.Color();
    symbols.forEach((s, i) => {
      c.set(kindColor[s.kind] ?? EMBER.star);
      colors.set([c.r, c.g, c.b], i * 3);
    });
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    this.satGroup = new THREE.Points(
      geo,
      new THREE.PointsMaterial({
        vertexColors: true,
        size: 7,
        sizeAttenuation: true,
        map: glowTexture(),
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      })
    );
    this.satGroup.frustumCulled = false;
    this.scene.add(this.satGroup);
    this.updateSatellitePositions(0);
  }

  private updateSatellitePositions(t: number): void {
    if (!this.satGroup) return;
    const pos = this.satGroup.geometry.getAttribute("position") as THREE.BufferAttribute;
    this.satMeta.forEach((s, i) => {
      const a = s.phase + t * s.speed;
      const lx = Math.cos(a) * s.r;
      const lz = Math.sin(a) * s.r;
      const ly = lz * Math.sin(s.tilt) * 0.8;
      pos.setXYZ(i, this.satCenter.x + lx, this.satCenter.y + ly, this.satCenter.z + lz * Math.cos(s.tilt));
    });
    pos.needsUpdate = true;
  }

  pickSatellite(raycaster: THREE.Raycaster): SatellitePick | null {
    if (!this.satGroup) return null;
    const prev = raycaster.params.Points?.threshold ?? 1;
    raycaster.params.Points = { threshold: 10 };
    const hits = raycaster.intersectObject(this.satGroup, false);
    raycaster.params.Points = { threshold: prev };
    const idx = hits[0]?.index;
    if (idx === undefined) return null;
    const meta = this.satMeta[idx];
    if (!meta) return null;
    const pick: SatellitePick = { symbolId: meta.symbolId };
    if (meta.line !== undefined) pick.line = meta.line;
    return pick;
  }

  labelAnchors(): { id: string; tint: string }[] {
    const out: { id: string; tint: string }[] = [];
    if (this.focusedFileId) out.push({ id: this.focusedFileId, tint: EMBER.star });
    for (const t of this.threads) {
      if (t.line.visible) out.push({ id: t.targetId, tint: t.tint });
    }
    return out;
  }

  drawSatelliteLabels(ctx: CanvasRenderingContext2D, camera: THREE.Camera, w: number, h: number): void {
    if (!this.satGroup || !this.satMeta.length) return;
    if (camera.position.distanceTo(this.satCenter) > 300) return;
    const pos = this.satGroup.geometry.getAttribute("position") as THREE.BufferAttribute;
    const v = new THREE.Vector3();
    ctx.font = "10px ui-monospace, Menlo, monospace";
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(255,244,234,0.75)";
    this.satMeta.forEach((s, i) => {
      v.set(pos.getX(i), pos.getY(i), pos.getZ(i)).project(camera);
      if (v.z > 1) return;
      ctx.fillText(s.name, ((v.x + 1) / 2) * w, ((1 - v.y) / 2) * h - 8);
    });
  }

  clearTrace(): void {
    for (const t of this.threads) {
      this.scene.remove(t.line, t.points);
      t.line.geometry.dispose();
      (t.line.material as THREE.Material).dispose();
      t.points.geometry.dispose();
      t.mat.dispose();
    }
    this.threads = [];
    this.focusedFileId = null;
  }

  clearSatellites(): void {
    if (this.satGroup) {
      this.scene.remove(this.satGroup);
      this.satGroup.geometry.dispose();
      (this.satGroup.material as THREE.PointsMaterial).dispose();
      this.satGroup = null;
    }
    this.satMeta = [];
  }

  private clearBeams(): void {
    for (const b of this.beams) {
      this.scene.remove(b.line, b.points);
      b.line.geometry.dispose();
      (b.line.material as THREE.Material).dispose();
      b.points.geometry.dispose();
      b.mat.dispose();
    }
    this.beams = [];
    this.bundleControls.clear();
  }

  setLayers(layers: Record<EdgeKind, boolean>): void {
    this.layers = { ...layers };
    for (const b of this.beams) {
      this.applyLayersTo(b.mat);
      b.line.visible = layers[b.lineKind];
    }
    for (const t of this.threads) {
      t.line.visible = t.points.visible = layers[t.kind];
    }
  }

  private applyLayersTo(mat: THREE.ShaderMaterial): void {
    (mat.uniforms.uKindOn!.value as THREE.Vector4).set(
      this.layers.Imports ? 1 : 0,
      this.layers.Calls ? 1 : 0,
      this.layers.Inherits ? 1 : 0,
      this.layers.References ? 1 : 0
    );
  }

  update(t: number, _dt: number): void {
    for (const b of this.beams) b.mat.uniforms.uTime!.value = t;
    for (const th of this.threads) th.mat.uniforms.uTime!.value = t;
    this.updateSatellitePositions(t);
  }

  dispose(): void {
    this.clearBeams();
    this.clearTrace();
    this.clearSatellites();
  }
}

let _glowTex: THREE.CanvasTexture | null = null;
function glowTexture(): THREE.CanvasTexture {
  if (_glowTex) return _glowTex;
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "#ffffff");
  grad.addColorStop(0.35, "#ffffffaa");
  grad.addColorStop(1, "transparent");
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  _glowTex = new THREE.CanvasTexture(c);
  return _glowTex;
}
