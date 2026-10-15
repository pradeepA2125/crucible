import { Component, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useMotionValueEvent, useScroll } from "framer-motion";
import * as THREE from "three";

type Props = { target: RefObject<HTMLDivElement | null>; paused: boolean; light: boolean; accent: string; secondary: string };
class SceneBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? null : this.props.children; }
}
function Ribbons({ progress, accent, secondary, light }: { progress: RefObject<number>; accent: string; secondary: string; light: boolean }) {
  const group = useRef<THREE.Group>(null);
  const { invalidate } = useThree();
  const geometries = useMemo(() => Array.from({ length: 3 }, (_, strand) => {
    const points = Array.from({ length: 161 }, (_, i) => {
      const t = i / 160 * Math.PI * 2;
      const r = 1.52 + .24 * Math.cos(3 * t + strand * 2.1);
      return new THREE.Vector3(r * Math.cos(t), r * Math.sin(t), .5 * Math.sin(2 * t + strand * 2.1));
    });
    return new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points, true), 160, .17, 10, true);
  }), []);
  useEffect(() => () => geometries.forEach(g => g.dispose()), [geometries]);
  useEffect(() => invalidate(), [accent, secondary, light, invalidate]);
  useFrame(() => {
    if (!group.current) return;
    const p = progress.current;
    group.current.rotation.set(.35 + p * .7, -.5 + p * 2.4, -.35 + p * .7);
    group.current.children.forEach((child, i) => {
      const spread = Math.sin(Math.min(1, p / .75) * Math.PI);
      child.position.set((i - 1) * spread * .65, (i - 1) * spread * .18, (i - 1) * .25);
      child.rotation.set(i * .8 + p * (i - 1), i * .6, i * .3);
    });
  });
  return <group ref={group}>{geometries.map((geometry, i) => <mesh key={i} geometry={geometry}><meshPhysicalMaterial color={i === 1 ? secondary : accent} metalness={.72} roughness={.23} clearcoat={1} clearcoatRoughness={.18} emissive={i === 1 ? secondary : accent} emissiveIntensity={light ? .08 : .12} /></mesh>)}</group>;
}
function Driver({ target, paused, progress }: { target: Props["target"]; paused: boolean; progress: RefObject<number> }) {
  const { invalidate } = useThree();
  const { scrollYProgress } = useScroll({ target, offset: ["start start", "end end"] });
  useMotionValueEvent(scrollYProgress, "change", value => {
    if (!paused && !document.hidden) { progress.current = value; invalidate(); }
  });
  useEffect(() => { if (!paused) { progress.current = scrollYProgress.get(); invalidate(); } }, [paused, progress, scrollYProgress, invalidate]);
  return null;
}
export default function Sculpture(props: Props) {
  const progress = useRef(0);
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    try {
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl2");
      setAvailable(!!gl);
      gl?.getExtension("WEBGL_lose_context")?.loseContext();
    } catch { setAvailable(false); }
  }, []);
  if (!available) return null;
  return <SceneBoundary><Canvas className="sculpture-canvas" frameloop="demand" dpr={[1, 1.5]} camera={{ position: [0, 0, 7.6], fov: 38 }} gl={{ alpha: true, antialias: true, powerPreference: "low-power" }} onCreated={({ gl }) => { gl.domElement.addEventListener("webglcontextlost", () => setAvailable(false), { once: true }); }}>
    <ambientLight intensity={props.light ? 1.6 : .7} /><directionalLight position={[2, 4, 5]} intensity={5} color="#f3eaff" /><directionalLight position={[-4, -1, 2]} intensity={3} color={props.secondary} /><directionalLight position={[0, 2, -3]} intensity={4} color={props.accent} />
    <Ribbons progress={progress} accent={props.accent} secondary={props.secondary} light={props.light} /><Driver target={props.target} paused={props.paused} progress={progress} />
  </Canvas></SceneBoundary>;
}
