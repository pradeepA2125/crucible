// Energy beams (bundles), selection trace threads, and L3 symbol satellites.
// Skeleton — the full particle systems land in the flows task; GraphScene's
// call sites are already final.
import type * as THREE from "three";
import type { Bundle, EdgeKind, FileDetail, SymbolDetail } from "../types";

export interface SatellitePick {
  symbolId: string;
  line?: number;
}

export class Flows {
  constructor(private readonly scene: THREE.Scene) {
    void this.scene;
  }

  setBundles(bundles: Bundle[], centroids: Map<string, [number, number, number]>): void {
    void bundles;
    void centroids;
  }

  traceFile(detail: FileDetail, posOf: (id: string) => [number, number, number] | null): void {
    void detail;
    void posOf;
  }

  traceSymbol(detail: SymbolDetail, posOf: (id: string) => [number, number, number] | null): void {
    void detail;
    void posOf;
  }

  showSatellites(detail: FileDetail, center: [number, number, number]): void {
    void detail;
    void center;
  }

  pickSatellite(raycaster: THREE.Raycaster): SatellitePick | null {
    void raycaster;
    return null;
  }

  labelAnchors(): { id: string; tint: string }[] {
    return [];
  }

  drawSatelliteLabels(
    ctx: CanvasRenderingContext2D,
    camera: THREE.Camera,
    w: number,
    h: number
  ): void {
    void ctx;
    void camera;
    void w;
    void h;
  }

  clearTrace(): void {}

  clearSatellites(): void {}

  setLayers(layers: Record<EdgeKind, boolean>): void {
    void layers;
  }

  update(t: number, dt: number): void {
    void t;
    void dt;
  }

  dispose(): void {}
}
