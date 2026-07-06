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

async function requestLayout(model: SpaceModel): Promise<LayoutResult> {
  try {
    const worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
    return await new Promise<LayoutResult>((resolve, reject) => {
      worker.onmessage = (ev: MessageEvent<LayoutResult>) => {
        worker.terminate();
        resolve(ev.data);
      };
      worker.onerror = (e) => {
        worker.terminate();
        reject(e);
      };
      worker.postMessage(model);
    });
  } catch {
    // Worker unavailable (restricted webview / jsdom) — compute synchronously.
    const { computeLayout } = await import("./layout");
    return computeLayout(model);
  }
}

export default function GraphApp({ createScene }: Props) {
  const [conn, setConn] = useState<Conn>({ kind: "connecting" });
  const [model, setModel] = useState<SpaceModel | null>(null);
  const [glFailed, setGlFailed] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sceneRef = useRef<SceneHandle | null>(null);
  const factoryRef = useRef<Props["createScene"] | null>(createScene ?? null);

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

  // Scene lifecycle: worker-computed layout, guarded against stale models.
  const layoutReqRef = useRef(0);
  useEffect(() => {
    if (!model || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const req = ++layoutReqRef.current;
    void (async () => {
      if (!factoryRef.current) {
        // Production path: lazy-load the Three.js factory so jsdom tests
        // (which always inject createScene) never touch WebGL code.
        const mod = await import("./scene/graph-scene");
        factoryRef.current = mod.createGraphScene;
      }
      if (!sceneRef.current) {
        try {
          sceneRef.current = factoryRef.current(canvas, {
            onPickStar: () => {},
            onPickPackage: () => {},
            onPickSatellite: () => {},
            onBackgroundClick: () => {},
          });
        } catch {
          // WebGL unavailable — plain-text structure fallback (spec: Failure modes).
          setGlFailed(true);
          return;
        }
      }
      const layout = await requestLayout(model);
      if (req !== layoutReqRef.current) return; // a newer model superseded this layout
      sceneRef.current?.setSpace(model, layout);
    })();
  }, [model]);

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
      {glFailed && model && (
        <div className="p-8 text-sm text-[var(--color-text-dim)] overflow-auto h-full">
          <div className="text-[10px] uppercase tracking-[0.3em] mb-4">
            WebGL unavailable — showing structure only
          </div>
          <ul className="space-y-1 font-mono text-xs">
            {model.packages.map((p) => (
              <li key={p.id}>
                {p.id} — {p.fileCount} files
              </li>
            ))}
          </ul>
        </div>
      )}
      <canvas
        ref={canvasRef}
        className="w-full h-full block"
        style={{ display: conn.kind === "ready" && !glFailed ? "block" : "none" }}
      />
    </div>
  );
}
