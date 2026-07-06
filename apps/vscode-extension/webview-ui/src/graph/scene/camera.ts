import type * as THREE from "three";
import { sphericalToPosition } from "../scene-math";

const SMOOTH = 4.2;
const MIN_RADIUS = 70;
const MAX_RADIUS = 4200;
const DRIFT_RATE = 0.038;

/** Orbit camera with smoothed goals: drag = yaw/pitch, wheel = dolly, flyTo = tween.
 * All motion converges via k = 1 - exp(-SMOOTH * dt) each frame. */
export class CameraRig {
  yaw = 0.8;
  pitch = 0.34;
  radius = 3400;
  target: [number, number, number] = [0, 0, 0];
  gYaw = 0.8;
  gPitch = 0.34;
  gRadius = 1350;
  gTarget: [number, number, number] = [0, 0, 0];
  drift = true;

  constructor(private readonly camera: THREE.PerspectiveCamera) {}

  update(dt: number): void {
    if (this.drift) this.gYaw += dt * DRIFT_RATE;
    const k = 1 - Math.exp(-SMOOTH * dt);
    this.yaw += (this.gYaw - this.yaw) * k;
    this.pitch += (this.gPitch - this.pitch) * k;
    this.radius += (this.gRadius - this.radius) * k;
    for (let i = 0; i < 3; i++) this.target[i] += (this.gTarget[i]! - this.target[i]!) * k;
    const [x, y, z] = sphericalToPosition(this.yaw, this.pitch, this.radius, this.target);
    this.camera.position.set(x, y, z);
    this.camera.lookAt(this.target[0], this.target[1], this.target[2]);
  }

  /** Pointer + wheel handling. onClick fires on pointerup with total drag < 6px,
   * passing client coords; returns a detach function. */
  attach(el: HTMLElement, onClick: (x: number, y: number) => void): () => void {
    let dragging = false;
    let moved = 0;
    let px = 0;
    let py = 0;
    const down = (e: PointerEvent) => {
      dragging = true;
      moved = 0;
      px = e.clientX;
      py = e.clientY;
      el.setPointerCapture(e.pointerId);
    };
    const move = (e: PointerEvent) => {
      if (!dragging) return;
      const dx = e.clientX - px;
      const dy = e.clientY - py;
      px = e.clientX;
      py = e.clientY;
      moved += Math.abs(dx) + Math.abs(dy);
      this.gYaw += dx * 0.0042;
      this.gPitch = Math.max(-1.25, Math.min(1.25, this.gPitch + dy * 0.0032));
      this.drift = false;
    };
    const up = (e: PointerEvent) => {
      dragging = false;
      if (moved < 6) onClick(e.clientX, e.clientY);
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      this.gRadius = Math.max(MIN_RADIUS, Math.min(MAX_RADIUS, this.gRadius * Math.exp(e.deltaY * 0.00095)));
    };
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("wheel", wheel, { passive: false });
    return () => {
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("wheel", wheel);
    };
  }

  flyTo(target: [number, number, number], radius: number): void {
    this.gTarget = [...target] as [number, number, number];
    this.gRadius = radius;
    this.drift = false;
  }

  reset(): void {
    this.gTarget = [0, 0, 0];
    this.gRadius = 1350;
    this.gPitch = 0.34;
    this.drift = true;
  }
}
