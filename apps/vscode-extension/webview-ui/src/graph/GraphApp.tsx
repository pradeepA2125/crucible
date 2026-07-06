import { useEffect, useRef, useState } from "react";
import { vscode } from "./vscodeApi";
import { EmptyState } from "./hud/EmptyState";
import type { GraphToWebview, LayoutResult, SceneCallbacks, SceneHandle, SpaceModel } from "./types";

interface Props {
  /** Injection seam: tests pass a fake; production defaults to the Three.js factory
   * (dynamically imported so jsdom tests never load WebGL code). */
  createScene?: (canvas: HTMLCanvasElement, cb: SceneCallbacks) => SceneHandle;
}

type Conn =
  | { kind: "connecting" }
  | { kind: "empty"; reason: "missing" | "malformed"; message: string; building: boolean }
  | { kind: "ready" };

export default function GraphApp({ createScene }: Props) {
  const [conn, setConn] = useState<Conn>({ kind: "connecting" });
  const [model, setModel] = useState<SpaceModel | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sceneRef = useRef<SceneHandle | null>(null);

  useEffect(() => {
    const onMsg = (ev: MessageEvent) => {
      const m = ev.data as GraphToWebview;
      if (m.type === "space") {
        setModel(m.model);
        setConn({ kind: "ready" });
      } else if (m.type === "noSnapshot") {
        setConn({ kind: "empty", reason: m.reason, message: m.message, building: m.building });
      }
      // spaceDiff / details / hits / error are wired in later tasks
    };
    window.addEventListener("message", onMsg);
    vscode.postMessage({ type: "ready" });
    return () => window.removeEventListener("message", onMsg);
  }, []);

  // While waiting on an index build, poll — the host watcher may not have armed
  // if .ai-editor/ didn't exist when the panel opened.
  useEffect(() => {
    if (conn.kind !== "empty" || !conn.building) return;
    const t = setInterval(() => vscode.postMessage({ type: "refresh" }), 3000);
    return () => clearInterval(t);
  }, [conn]);

  // Scene lifecycle + naive layout (worker layout replaces this in the layout task).
  useEffect(() => {
    if (!model || !canvasRef.current || !createScene) return;
    if (!sceneRef.current) {
      sceneRef.current = createScene(canvasRef.current, {
        onPickStar: () => {},
        onPickPackage: () => {},
        onPickSatellite: () => {},
        onBackgroundClick: () => {},
      });
    }
    const layout: LayoutResult = {
      ids: model.stars.map((s) => s.id),
      positions: new Float32Array(model.stars.length * 3),
    };
    sceneRef.current.setSpace(model, layout);
  }, [model, createScene]);

  useEffect(() => () => sceneRef.current?.dispose(), []);

  return (
    <div className="w-screen h-screen overflow-hidden" style={{ background: "#070203" }}>
      {conn.kind === "connecting" && (
        <div className="flex items-center justify-center h-full text-xs uppercase tracking-[0.3em] text-[var(--color-text-dim)]">
          mapping the space…
        </div>
      )}
      {conn.kind === "empty" && (
        <EmptyState
          reason={conn.reason}
          message={conn.message}
          building={conn.building}
          onBuild={() => vscode.postMessage({ type: "buildIndex" })}
        />
      )}
      <canvas
        ref={canvasRef}
        className="w-full h-full block"
        style={{ display: conn.kind === "ready" ? "block" : "none" }}
      />
    </div>
  );
}
