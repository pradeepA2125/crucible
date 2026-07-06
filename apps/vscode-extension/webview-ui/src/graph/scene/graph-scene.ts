// Owns renderer/composer/frame-loop/picking/labels. GraphApp only sees SceneHandle.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { EMBER } from "../palette";
import type {
  EdgeKind,
  FileDetail,
  FocusState,
  LayoutResult,
  SceneCallbacks,
  SceneHandle,
  SpaceModel,
  SymbolDetail,
} from "../types";
import { CameraRig } from "./camera";
import { computeCentroids, Starfield } from "./starfield";
import { Flows } from "./flows";

export function createGraphScene(canvas: HTMLCanvasElement, cb: SceneCallbacks): SceneHandle {
  return new GraphScene(canvas, cb);
}

class GraphScene implements SceneHandle {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private rig: CameraRig;
  private composer: EffectComposer;
  private starfield: Starfield;
  private flows: Flows;
  private labelCanvas: HTMLCanvasElement;
  private labelCtx: CanvasRenderingContext2D;
  private raycaster = new THREE.Raycaster();
  private model: SpaceModel | null = null;
  private focus: FocusState = { level: 0 };
  private raf = 0;
  private tPrev = performance.now();
  private elapsed = 0;
  private detachInput: () => void;
  private resizeObs: ResizeObserver;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly cb: SceneCallbacks
  ) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.scene.background = new THREE.Color(EMBER.bgBot);
    this.scene.fog = new THREE.FogExp2(EMBER.bgBot, 0.00045);
    this.camera = new THREE.PerspectiveCamera(55, 1, 1, 20000);
    this.rig = new CameraRig(this.camera);
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.composer.addPass(new UnrealBloomPass(new THREE.Vector2(1, 1), 0.9, 0.7, 0.12));
    this.starfield = new Starfield(this.scene);
    this.flows = new Flows(this.scene);
    this.raycaster.params.Points = { threshold: 14 };

    // Screen-space label overlay: a sibling 2D canvas positioned over the GL one.
    this.labelCanvas = document.createElement("canvas");
    this.labelCanvas.style.cssText = "position:absolute;inset:0;pointer-events:none;";
    canvas.parentElement!.style.position = "relative";
    canvas.parentElement!.appendChild(this.labelCanvas);
    this.labelCtx = this.labelCanvas.getContext("2d")!;

    this.detachInput = this.rig.attach(canvas, (x, y) => this.pick(x, y));
    this.resizeObs = new ResizeObserver(() => this.resize());
    this.resizeObs.observe(canvas.parentElement!);
    this.resize();
    this.loop();
  }

  private resize(): void {
    const w = this.canvas.parentElement!.clientWidth || 1;
    const h = this.canvas.parentElement!.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.labelCanvas.width = w * window.devicePixelRatio;
    this.labelCanvas.height = h * window.devicePixelRatio;
    this.labelCanvas.style.width = `${w}px`;
    this.labelCanvas.style.height = `${h}px`;
    this.labelCtx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  private loop = (): void => {
    this.raf = requestAnimationFrame(this.loop);
    const now = performance.now();
    const dt = Math.min(0.05, (now - this.tPrev) / 1000);
    this.tPrev = now;
    this.elapsed += dt;
    const t = this.elapsed;
    this.rig.update(dt);
    this.starfield.update(t);
    this.flows.update(t, dt);
    this.composer.render();
    this.drawLabels();
  };

  private pick(x: number, y: number): void {
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((x - rect.left) / rect.width) * 2 - 1,
      -((y - rect.top) / rect.height) * 2 + 1
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    const sat = this.flows.pickSatellite(this.raycaster);
    if (sat) {
      this.cb.onPickSatellite(sat.symbolId, sat.line);
      return;
    }
    const hits = this.raycaster.intersectObject(this.starfield.points, false);
    if (hits.length && hits[0]!.index !== undefined) {
      const star = this.starfield.starAt(hits[0]!.index!);
      if (star) {
        this.cb.onPickStar(star.id);
        return;
      }
    }
    // Nebula label pick: nearest package centroid within 60px screen distance
    const pkg = this.nearestPackage(x - rect.left, y - rect.top, 60);
    if (pkg) this.cb.onPickPackage(pkg);
    else this.cb.onBackgroundClick();
  }

  private nearestPackage(sx: number, sy: number, maxPx: number): string | null {
    let best: string | null = null;
    let bd = maxPx;
    const v = new THREE.Vector3();
    for (const [pkg, [x, y, z]] of this.starfield.pkgCentroids()) {
      v.set(x, y, z).project(this.camera);
      if (v.z > 1) continue;
      const px = ((v.x + 1) / 2) * this.canvas.clientWidth;
      const py = ((1 - v.y) / 2) * this.canvas.clientHeight;
      const d = Math.hypot(px - sx, py - sy);
      if (d < bd) {
        bd = d;
        best = pkg;
      }
    }
    return best;
  }

  private drawLabels(): void {
    const ctx = this.labelCtx;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    if (!this.model) return;
    const v = new THREE.Vector3();
    ctx.font = "600 11px ui-sans-serif, system-ui";
    ctx.textAlign = "center";
    const pkgOrder = this.model.packages.map((p) => p.id);
    for (const [pkg, [x, y, z]] of this.starfield.pkgCentroids()) {
      v.set(x, y, z).project(this.camera);
      if (v.z > 1) continue;
      const px = ((v.x + 1) / 2) * w;
      const py = ((1 - v.y) / 2) * h - 30;
      const dimmed = this.focus.level >= 1 && (this.focus as { pkg?: string }).pkg !== pkg;
      ctx.globalAlpha = dimmed ? 0.25 : 0.85;
      const idx = pkgOrder.indexOf(pkg);
      ctx.fillStyle = EMBER.clusterTints[(idx < 0 ? 0 : idx) % EMBER.clusterTints.length]!;
      ctx.fillText(pkg.toUpperCase(), px, py);
    }
    ctx.globalAlpha = 1;
    // Focused star + trace target labels
    ctx.font = "11px ui-monospace, Menlo, monospace";
    for (const { id, tint } of this.flows.labelAnchors()) {
      const p = this.starfield.positionOf(id);
      if (!p) continue;
      v.set(p[0], p[1], p[2]).project(this.camera);
      if (v.z > 1) continue;
      const px = ((v.x + 1) / 2) * w;
      const py = ((1 - v.y) / 2) * h + 18;
      const short = id.slice(id.lastIndexOf("/") + 1);
      ctx.fillStyle = "rgba(7,2,3,0.72)";
      const tw = ctx.measureText(short).width;
      ctx.fillRect(px - tw / 2 - 5, py - 11, tw + 10, 15);
      ctx.fillStyle = tint;
      ctx.fillText(short, px, py);
    }
    this.flows.drawSatelliteLabels(ctx, this.camera, w, h);
  }

  // ---- SceneHandle ----
  setSpace(model: SpaceModel, layout: LayoutResult): void {
    this.model = model;
    const pkgOrder = model.packages.map((p) => p.id);
    this.starfield.setStars(model.stars, layout, pkgOrder);
    this.flows.setBundles(model.bundles, this.starfield.pkgCentroids());
  }

  morph(model: SpaceModel, layout: LayoutResult, removed: string[]): void {
    this.model = model;
    const pkgOrder = model.packages.map((p) => p.id);
    this.starfield.morphTo(model.stars, layout, pkgOrder, removed, this.elapsed);
    // Beams re-anchor to the NEW layout's centroids immediately (the starfield
    // geometry rebuild may be deferred behind the removed-star fade).
    this.flows.setBundles(model.bundles, computeCentroids(model.stars, layout));
  }

  setFocus(focus: FocusState): void {
    this.focus = focus;
    if (focus.level === 0) {
      this.starfield.setDimSet(null, null);
      this.flows.clearTrace();
      this.flows.clearSatellites();
    } else if (focus.level === 1) {
      this.starfield.setDimSet(null, focus.pkg);
      this.flows.clearTrace();
      this.flows.clearSatellites();
    }
    // level 2/3 dim sets are applied by showFileTrace/showSatellites (they know the targets)
  }

  showFileTrace(detail: FileDetail): void {
    const keep = new Set<string>([detail.fileId]);
    for (const e of detail.edges) keep.add(e.otherFile);
    this.starfield.setDimSet(keep, null);
    this.flows.traceFile(detail, (id) => this.starfield.positionOf(id));
  }

  showSatellites(detail: FileDetail): void {
    const center = this.starfield.positionOf(detail.fileId);
    if (center) this.flows.showSatellites(detail, center);
  }

  showSymbolTrace(detail: SymbolDetail): void {
    this.flows.traceSymbol(detail, (id) => this.starfield.positionOf(id));
  }

  clearOverlays(): void {
    this.flows.clearTrace();
    this.flows.clearSatellites();
  }

  setLayers(layers: Record<EdgeKind, boolean>): void {
    this.flows.setLayers(layers);
  }

  flyToStar(id: string, radius = 300): void {
    const p = this.starfield.positionOf(id);
    if (p) this.rig.flyTo(p, radius);
  }

  framePackage(pkg: string): void {
    const c = this.starfield.pkgCentroids().get(pkg);
    if (c) this.rig.flyTo(c, 520);
  }

  resetCamera(): void {
    this.rig.reset();
  }

  dispose(): void {
    cancelAnimationFrame(this.raf);
    this.detachInput();
    this.resizeObs.disconnect();
    this.starfield.dispose();
    this.flows.dispose();
    this.composer.dispose();
    this.renderer.dispose();
    this.labelCanvas.remove();
  }
}
