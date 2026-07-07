import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { CameraRig } from "./camera";

describe("CameraRig.followPath", () => {
  it("drives the target along the points and fires onDone at the end", () => {
    const rig = new CameraRig(new THREE.PerspectiveCamera(55, 1, 1, 20000));
    rig.drift = true;
    let done = false;
    rig.followPath(
      [
        [0, 0, 0],
        [100, 0, 0],
        [200, 0, 0],
      ],
      1,
      280,
      () => {
        done = true;
      }
    );
    expect(rig.drift).toBe(false);
    // halfway: goal target should have left the origin toward the path
    for (let i = 0; i < 5; i++) rig.update(0.1);
    expect(done).toBe(false);
    expect(rig.gTarget[0]).toBeGreaterThan(0);
    // finish the ride
    for (let i = 0; i < 8; i++) rig.update(0.1);
    expect(done).toBe(true);
    expect(rig.gTarget).toEqual([200, 0, 0]);
    expect(rig.gRadius).toBe(280);
  });

  it("ignores degenerate paths", () => {
    const rig = new CameraRig(new THREE.PerspectiveCamera(55, 1, 1, 20000));
    let done = false;
    rig.followPath([[0, 0, 0]], 1, 280, () => {
      done = true;
    });
    rig.update(0.1);
    expect(done).toBe(false);
  });
});
