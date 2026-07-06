import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { aggregateToDirs, applyDiff, LOD_STAR_THRESHOLD } from "./lod";
import { vscode } from "./vscodeApi";
import { EmptyState } from "./hud/EmptyState";
import { Breadcrumb } from "./hud/Breadcrumb";
import { Legend } from "./hud/Legend";
import { EdgeLayers } from "./hud/EdgeLayers";
import { InfoCard } from "./hud/InfoCard";
import { SearchBar } from "./hud/SearchBar";
import { graphReducer, initialGraphState } from "./useGraphState";
import type {
  GraphToWebview,
  LayoutResult,
  SceneCallbacks,
  SceneHandle,
  SpaceModel,
} from "./types";

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
  const [staleAgeSec, setStaleAgeSec] = useState<number | null>(null);
  const [glFailed, setGlFailed] = useState(false);
  const morphRef = useRef<{ isDiff: boolean; removed: string[] }>({ isDiff: false, removed: [] });
  const [state, dispatch] = useReducer(graphReducer, undefined, initialGraphState);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sceneRef = useRef<SceneHandle | null>(null);
  const factoryRef = useRef<Props["createScene"] | null>(createScene ?? null);
  const modelRef = useRef<SpaceModel | null>(null);
  const stateRef = useRef(state);
  const lastSatPickRef = useRef<{ id: string; at: number }>({ id: "", at: 0 });
  modelRef.current = model;
  stateRef.current = state;

  // Host message bus.
  useEffect(() => {
    const onMsg = (ev: MessageEvent) => {
      const m = ev.data as GraphToWebview;
      if (m.type === "space") {
        morphRef.current = { isDiff: false, removed: [] };
        setModel(m.model);
        setStaleAgeSec(m.staleAgeSec);
        setConn({ kind: "ready" });
      } else if (m.type === "spaceDiff") {
        const prev = modelRef.current;
        if (!prev) return;
        morphRef.current = { isDiff: true, removed: m.diff.removed };
        setModel(applyDiff(prev, m.diff));
        setStaleAgeSec(null); // a fresh snapshot just landed
        // If the focused file vanished, pop focus back to its package.
        const focus = stateRef.current.focus;
        if ((focus.level === 2 || focus.level === 3) && m.diff.removed.includes(focus.fileId)) {
          dispatch({ type: "pickPackage", pkg: focus.pkg });
        }
      } else if (m.type === "noSnapshot") {
        setConn({ kind: "empty", reason: m.reason, message: m.message, building: m.building });
      } else if (m.type === "fileDetail") {
        dispatch({ type: "hostFileDetail", detail: m.detail });
      } else if (m.type === "symbolDetail") {
        dispatch({ type: "hostSymbolDetail", detail: m.detail });
      } else if (m.type === "symbolHits") {
        dispatch({ type: "hostSymbolHits", hits: m.hits });
      } else if (m.type === "error") {
        dispatch({ type: "hostError", message: m.message });
      }
      // spaceDiff is wired in the live-refresh task
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

  // Escape pops one focus level.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dispatch({ type: "pop" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Monster-repo LOD: above the threshold, L0 renders directory-level aggregate
  // stars; any deeper focus swaps the real file stars back in.
  const renderModel = useMemo(() => {
    if (!model) return null;
    if (model.stars.length > LOD_STAR_THRESHOLD && state.focus.level === 0) {
      return aggregateToDirs(model);
    }
    return model;
  }, [model, state.focus.level]);

  // Scene lifecycle: worker-computed layout, guarded against stale models.
  const layoutReqRef = useRef(0);
  useEffect(() => {
    const model = renderModel;
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
        const callbacks: SceneCallbacks = {
          onPickStar: (id) => {
            if (id.startsWith("dir:")) {
              // LOD aggregate star -> route to its package focus.
              const agg = modelRef.current ? aggregateToDirs(modelRef.current) : null;
              const pkg = agg?.stars.find((s) => s.id === id)?.pkg;
              if (pkg) dispatch({ type: "pickPackage", pkg });
              return;
            }
            const star = modelRef.current?.stars.find((s) => s.id === id);
            if (!star) return;
            dispatch({ type: "pickStar", fileId: id, pkg: star.pkg });
            sceneRef.current?.flyToStar(id, 300);
          },
          onPickPackage: (pkg) => {
            dispatch({ type: "pickPackage", pkg });
            sceneRef.current?.framePackage(pkg);
          },
          onPickSatellite: (symbolId, line) => {
            const focus = stateRef.current.focus;
            if (focus.level !== 3) return;
            const now = Date.now();
            const last = lastSatPickRef.current;
            if (last.id === symbolId && now - last.at < 400) {
              // double-pick -> open at the symbol's line
              vscode.postMessage({ type: "openFile", path: focus.fileId, ...(line ? { line } : {}) });
            } else {
              dispatch({ type: "pickSymbol", symbolId });
            }
            lastSatPickRef.current = { id: symbolId, at: now };
          },
          onBackgroundClick: () => dispatch({ type: "pop" }),
        };
        try {
          sceneRef.current = factoryRef.current(canvas, callbacks);
        } catch {
          // WebGL unavailable — plain-text structure fallback (spec: Failure modes).
          setGlFailed(true);
          return;
        }
      }
      const layout = await requestLayout(model);
      if (req !== layoutReqRef.current) return; // a newer model superseded this layout
      const morph = morphRef.current;
      if (morph.isDiff) {
        morphRef.current = { isDiff: false, removed: [] };
        sceneRef.current?.morph(model, layout, morph.removed);
      } else {
        sceneRef.current?.setSpace(model, layout);
      }
    })();
  }, [renderModel]);

  // Focus -> scene: dimming, camera, detail requests.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    scene.setFocus(state.focus);
    if (state.focus.level === 0) scene.resetCamera();
    if (state.focus.level === 2 || state.focus.level === 3) {
      vscode.postMessage({ type: "fileDetail", fileId: state.focus.fileId });
    }
  }, [state.focus]);

  // fileDetail arrived -> trace threads (and satellites when diving).
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || !state.fileDetail) return;
    scene.showFileTrace(state.fileDetail);
    if (state.focus.level === 3) {
      scene.showSatellites(state.fileDetail);
      scene.flyToStar(state.fileDetail.fileId, 120);
    }
  }, [state.fileDetail, state.focus]);

  // Symbol picked at L3 -> fetch + trace its edges.
  useEffect(() => {
    if (state.focus.level === 3 && state.focus.symbolId) {
      vscode.postMessage({ type: "symbolDetail", symbolId: state.focus.symbolId });
    }
  }, [state.focus]);
  useEffect(() => {
    if (state.symbolDetail) sceneRef.current?.showSymbolTrace(state.symbolDetail);
  }, [state.symbolDetail]);

  // Layer toggles.
  useEffect(() => {
    sceneRef.current?.setLayers(state.layers);
  }, [state.layers]);

  useEffect(() => () => sceneRef.current?.dispose(), []);

  const focusedStar =
    (state.focus.level === 2 || state.focus.level === 3) && model
      ? (model.stars.find((s) => s.id === (state.focus as { fileId: string }).fileId) ?? null)
      : null;

  return (
    <div className="w-screen h-screen overflow-hidden relative" style={{ background: "#070203" }}>
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
      {conn.kind === "ready" && !glFailed && (
        <>
          <Breadcrumb
            focus={state.focus}
            onPop={() => dispatch({ type: "pop" })}
            onReset={() => dispatch({ type: "reset" })}
          />
          {staleAgeSec !== null && (
            <div
              className="absolute top-16 left-4 px-3 py-1.5 rounded-lg text-[10px] uppercase tracking-[0.15em]
                         bg-[rgba(22,7,9,0.6)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md
                         text-[#fbbf24] opacity-80"
            >
              index stale · {Math.round(staleAgeSec / 60)}m — watching for rebuild
            </div>
          )}
          <Legend />
          <EdgeLayers
            layers={state.layers}
            focusLevel={state.focus.level}
            onToggle={(kind, on) => dispatch({ type: "setLayer", kind, on })}
          />
          {focusedStar && (
            <InfoCard
              star={focusedStar}
              detail={state.fileDetail}
              onOpen={() => vscode.postMessage({ type: "openFile", path: focusedStar.id })}
              onDive={() => dispatch({ type: "dive" })}
            />
          )}
          <SearchBar
            stars={model?.stars ?? []}
            symbolHits={state.searchHits}
            onQuerySymbols={(query) => vscode.postMessage({ type: "searchSymbols", query })}
            onGoFile={(fileId) => {
              const star = modelRef.current?.stars.find((s) => s.id === fileId);
              if (!star) return;
              dispatch({ type: "pickStar", fileId, pkg: star.pkg });
              sceneRef.current?.flyToStar(fileId, 260);
            }}
            onGoSymbol={(hit) => {
              const star = modelRef.current?.stars.find((s) => s.id === hit.fileId);
              if (!star) return;
              dispatch({ type: "pickStar", fileId: hit.fileId, pkg: star.pkg });
              dispatch({ type: "dive" });
              dispatch({ type: "pickSymbol", symbolId: hit.symbolId });
              sceneRef.current?.flyToStar(hit.fileId, 120);
            }}
          />
        </>
      )}
    </div>
  );
}
