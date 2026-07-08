import { useMemo, useRef, type RefObject } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import type { SpaceTheme } from "../theme";

/**
 * The hero object: the workspace rendered as a dependency space — the same
 * "code as a galaxy" idea as the editor's built-in Axon visualizer. Clusters
 * are real monorepo packages, nebulae are their gravity, pulses are energy
 * moving along call/import edges, and a ghost twin of the whole graph floats
 * behind it: the shadow workspace.
 */

export type EdgeKind = "imports" | "calls" | "inherits";
export type LayerState = Record<EdgeKind, boolean>;

const CLUSTER_NAMES = [
  "services/agentd",
  "services/indexer-rs",
  "apps/vscode-extension",
  "apps/editor-client",
  "webview-ui",
  "scripts",
  "docs",
];

const PER_CLUSTER = 24;
const PULSE_COUNT = 26;
const DUST_COUNT = 700;
const BASE_Z = 7.4;

interface TopEdge {
  a: number;
  b: number;
  kind: EdgeKind;
}

interface Topology {
  nodes: THREE.Vector3[];
  centers: THREE.Vector3[];
  edges: TopEdge[];
  beaconIndex: number;
}

function buildTopology(): Topology {
  const nodes: THREE.Vector3[] = [];
  const centers: THREE.Vector3[] = [];

  for (let c = 0; c < CLUSTER_NAMES.length; c++) {
    const angle = (c / CLUSTER_NAMES.length) * Math.PI * 2 + Math.random() * 0.5;
    const radius = 2.5 + Math.random() * 1.2;
    const center = new THREE.Vector3(
      Math.cos(angle) * radius,
      (Math.random() - 0.5) * 2.2,
      Math.sin(angle) * radius * 0.8,
    );
    centers.push(center);
    for (let i = 0; i < PER_CLUSTER; i++) {
      const spread = 1.75 * Math.pow(Math.random(), 0.62);
      nodes.push(
        center
          .clone()
          .add(new THREE.Vector3().randomDirection().multiplyScalar(spread)),
      );
    }
  }

  const edges: TopEdge[] = [];
  for (let c = 0; c < CLUSTER_NAMES.length; c++) {
    const start = c * PER_CLUSTER;
    for (let i = 0; i < PER_CLUSTER; i++) {
      const a = start + i;
      const ranked: Array<[number, number]> = [];
      for (let j = 0; j < PER_CLUSTER; j++) {
        if (i === j) continue;
        const b = start + j;
        ranked.push([nodes[a].distanceToSquared(nodes[b]), b]);
      }
      ranked.sort((x, y) => x[0] - y[0]);
      for (let k = 0; k < 2; k++) {
        edges.push({
          a,
          b: ranked[k][1],
          kind: Math.random() < 0.16 ? "inherits" : "calls",
        });
      }
    }
  }
  for (let c = 0; c < CLUSTER_NAMES.length; c++) {
    for (let k = 0; k < 3; k++) {
      const a = c * PER_CLUSTER + Math.floor(Math.random() * PER_CLUSTER);
      const other =
        (c + 1 + Math.floor(Math.random() * (CLUSTER_NAMES.length - 1))) %
        CLUSTER_NAMES.length;
      const b = other * PER_CLUSTER + Math.floor(Math.random() * PER_CLUSTER);
      edges.push({ a, b, kind: "imports" });
    }
  }

  // the entry-point beacon lives in services/agentd
  const beaconIndex = Math.floor(Math.random() * PER_CLUSTER);

  return { nodes, centers, edges, beaconIndex };
}

function makeRingTexture(): THREE.Texture {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  ctx.strokeStyle = "rgba(255,255,255,0.9)";
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - 4, 0, Math.PI * 2);
  ctx.stroke();
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

function makeGlowTexture(): THREE.Texture {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.35, "rgba(255,255,255,0.55)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

function geometryFromPositions(positions: Float32Array): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  return geometry;
}

function edgeGeometry(topology: Topology, kind: EdgeKind): THREE.BufferGeometry {
  const subset = topology.edges.filter((e) => e.kind === kind);
  const positions = new Float32Array(subset.length * 6);
  subset.forEach((edge, i) => {
    topology.nodes[edge.a].toArray(positions, i * 6);
    topology.nodes[edge.b].toArray(positions, i * 6 + 3);
  });
  return geometryFromPositions(positions);
}

interface PulseState {
  edge: number;
  t: number;
  speed: number;
}

interface SceneProps {
  theme: SpaceTheme;
  layers: LayerState;
  zoom: RefObject<number>;
}

function Scene({ theme, layers, zoom }: SceneProps) {
  const topology = useMemo(buildTopology, []);
  const glow = useMemo(makeGlowTexture, []);
  const ring = useMemo(makeRingTexture, []);
  const main = useRef<THREE.Group>(null);
  const ghost = useRef<THREE.Group>(null);
  const beaconCore = useRef<THREE.Sprite>(null);
  const beaconRing = useRef<THREE.Sprite>(null);
  const nodeMaterial = useRef<THREE.PointsMaterial>(null);
  const dustMaterial = useRef<THREE.PointsMaterial>(null);

  const nodeGeometry = useMemo(() => {
    const positions = new Float32Array(topology.nodes.length * 3);
    topology.nodes.forEach((n, i) => n.toArray(positions, i * 3));
    const geometry = geometryFromPositions(positions);
    return geometry;
  }, [topology]);

  // colors rebuilt on theme swap; layout stays put (like Axon's theme picker)
  useMemo(() => {
    const colors = new Float32Array(topology.nodes.length * 3);
    const color = new THREE.Color();
    for (let i = 0; i < topology.nodes.length; i++) {
      color.set(
        theme.nodePalette[Math.floor((i * 2654435761) % theme.nodePalette.length)],
      );
      colors.set([color.r, color.g, color.b], i * 3);
    }
    nodeGeometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  }, [topology, theme, nodeGeometry]);

  const edgeGeometries = useMemo(
    () => ({
      imports: edgeGeometry(topology, "imports"),
      calls: edgeGeometry(topology, "calls"),
      inherits: edgeGeometry(topology, "inherits"),
    }),
    [topology],
  );

  const dustGeometry = useMemo(() => {
    const positions = new Float32Array(DUST_COUNT * 3);
    for (let i = 0; i < DUST_COUNT; i++) {
      new THREE.Vector3()
        .randomDirection()
        .multiplyScalar(3 + Math.random() * 6)
        .toArray(positions, i * 3);
    }
    return geometryFromPositions(positions);
  }, []);

  const pulseGeometry = useMemo(
    () => geometryFromPositions(new Float32Array(PULSE_COUNT * 3)),
    [],
  );
  const pulses = useRef<PulseState[]>(
    Array.from({ length: PULSE_COUNT }, () => ({
      edge: Math.floor(Math.random() * topology.edges.length),
      t: Math.random(),
      speed: 0.25 + Math.random() * 0.55,
    })),
  );

  const beaconPosition = topology.nodes[topology.beaconIndex];

  useFrame((state, delta) => {
    const group = main.current;
    if (group) {
      group.rotation.y += delta * 0.045;
      group.rotation.x = THREE.MathUtils.lerp(
        group.rotation.x,
        state.pointer.y * 0.13,
        0.025,
      );
      // keep the space weighted right-of-center so the headline stays calm
      group.position.x = THREE.MathUtils.lerp(
        group.position.x,
        1.35 + state.pointer.x * 0.42,
        0.025,
      );
    }
    if (ghost.current && group) {
      ghost.current.rotation.y = group.rotation.y * 0.88 - 0.3;
      ghost.current.rotation.x = group.rotation.x * 0.8;
    }

    // scroll pull-back: leaving the hero flies the camera out of the space
    state.camera.position.z = BASE_Z + (zoom.current ?? 0) * 3.2;

    const t = state.clock.elapsedTime;
    if (beaconCore.current) {
      const s = 1.15 + Math.sin(t * 2.1) * 0.14;
      beaconCore.current.scale.set(s, s, 1);
    }
    if (beaconRing.current) {
      const pulse = (t * 0.5) % 1;
      const s = 0.5 + pulse * 2.4;
      beaconRing.current.scale.set(s, s, 1);
      (beaconRing.current.material as THREE.SpriteMaterial).opacity =
        (1 - pulse) * 0.45;
    }
    if (nodeMaterial.current) {
      nodeMaterial.current.opacity = 0.86 + Math.sin(t * 1.3) * 0.1;
    }
    if (dustMaterial.current) {
      dustMaterial.current.opacity = 0.28 + Math.sin(t * 0.6) * 0.08;
    }

    const attr = pulseGeometry.getAttribute("position") as THREE.BufferAttribute;
    const scratch = new THREE.Vector3();
    pulses.current.forEach((pulse, i) => {
      pulse.t += delta * pulse.speed;
      if (pulse.t >= 1) {
        pulse.t = 0;
        pulse.edge = Math.floor(Math.random() * topology.edges.length);
        pulse.speed = 0.25 + Math.random() * 0.55;
      }
      const edge = topology.edges[pulse.edge];
      if (!layers[edge.kind]) {
        attr.setXYZ(i, 9999, 9999, 9999);
        return;
      }
      scratch.lerpVectors(topology.nodes[edge.a], topology.nodes[edge.b], pulse.t);
      attr.setXYZ(i, scratch.x, scratch.y, scratch.z);
    });
    attr.needsUpdate = true;
  });

  const edgeColor: Record<EdgeKind, string> = {
    imports: theme.accent,
    calls: theme.secondary,
    inherits: theme.tertiary,
  };
  const edgeOpacity: Record<EdgeKind, number> = {
    imports: 0.12,
    calls: 0.07,
    inherits: 0.1,
  };

  return (
    <>
      <group ref={main}>
        {/* nebulae — one gravity well per package */}
        {topology.centers.map((center, i) => (
          <sprite key={i} position={center} scale={[3.4 + (i % 3) * 0.9, 3.4 + (i % 3) * 0.9, 1]}>
            <spriteMaterial
              map={glow}
              color={theme.nebulae[i % theme.nebulae.length]}
              transparent
              opacity={0.14}
              depthWrite={false}
              blending={THREE.AdditiveBlending}
            />
          </sprite>
        ))}

        {/* package labels — screen-space HUD text pinned to cluster cores */}
        {topology.centers.map((center, i) => (
          <Html
            key={`label-${i}`}
            position={[center.x, center.y + 0.55, center.z]}
            center
            wrapperClass="ds-label-wrapper"
            zIndexRange={[5, 0]}
          >
            <div
              className="ds-label"
              style={{ color: theme.nebulae[i % theme.nebulae.length] }}
            >
              <span
                className="ds-label-dot"
                style={{ background: "currentcolor" }}
              />
              {CLUSTER_NAMES[i]}
            </div>
          </Html>
        ))}

        <points geometry={nodeGeometry}>
          <pointsMaterial
            ref={nodeMaterial}
            map={glow}
            vertexColors
            transparent
            opacity={0.95}
            size={0.15}
            sizeAttenuation
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </points>

        {(Object.keys(edgeGeometries) as EdgeKind[]).map((kind) => (
          <lineSegments
            key={kind}
            geometry={edgeGeometries[kind]}
            visible={layers[kind]}
          >
            <lineBasicMaterial
              color={edgeColor[kind]}
              transparent
              opacity={edgeOpacity[kind]}
              depthWrite={false}
              blending={THREE.AdditiveBlending}
            />
          </lineSegments>
        ))}

        <points geometry={pulseGeometry}>
          <pointsMaterial
            map={glow}
            color={theme.accentInk}
            transparent
            opacity={0.9}
            size={0.3}
            sizeAttenuation
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </points>

        {/* entry-point beacon: core + lens streak */}
        <sprite ref={beaconCore} position={beaconPosition}>
          <spriteMaterial
            map={glow}
            color={theme.beacon}
            transparent
            opacity={0.95}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </sprite>
        <sprite position={beaconPosition} scale={[5.2, 0.05, 1]}>
          <spriteMaterial
            map={glow}
            color={theme.beacon}
            transparent
            opacity={0.4}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </sprite>
        <sprite ref={beaconRing} position={beaconPosition}>
          <spriteMaterial
            map={ring}
            color={theme.beacon}
            transparent
            opacity={0.4}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </sprite>
      </group>

      {/* interstellar dust */}
      <points geometry={dustGeometry}>
        <pointsMaterial
          ref={dustMaterial}
          map={glow}
          color="#8b8b99"
          transparent
          opacity={0.3}
          size={0.035}
          sizeAttenuation
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </points>

      {/* the shadow twin — the whole graph again, ghosted, drifting behind */}
      <group ref={ghost} position={[1.9, -0.9, -3.4]}>
        <points geometry={nodeGeometry}>
          <pointsMaterial
            map={glow}
            color={theme.accent}
            transparent
            opacity={0.1}
            size={0.1}
            sizeAttenuation
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </points>
        <lineSegments geometry={edgeGeometries.calls}>
          <lineBasicMaterial
            color={theme.accent}
            transparent
            opacity={0.04}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </lineSegments>
      </group>
    </>
  );
}

export default function DependencySpace(props: SceneProps) {
  return (
    <Canvas
      dpr={[1, 1.8]}
      camera={{ position: [0, 0, BASE_Z], fov: 50 }}
      gl={{ alpha: true, antialias: true, powerPreference: "high-performance" }}
      style={{ position: "absolute", inset: 0 }}
    >
      <fog attach="fog" args={[props.theme.bg, 7, 16]} />
      <Scene {...props} />
    </Canvas>
  );
}
