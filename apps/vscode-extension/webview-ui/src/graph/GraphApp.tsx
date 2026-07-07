import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { aggregateToDirs, applyDiff, LOD_STAR_THRESHOLD } from "./lod";
import { vscode } from "./vscodeApi";
import { EmptyState } from "./hud/EmptyState";
import { Breadcrumb } from "./hud/Breadcrumb";
import { Legend } from "./hud/Legend";
import { EdgeLayers } from "./hud/EdgeLayers";
import { InfoCard } from "./hud/InfoCard";
import { SearchBar } from "./hud/SearchBar";
import { ViewPanel } from "./hud/ViewPanel";
import { DEFAULT_PALETTE, isPaletteName, PALETTES, type Palette, type PaletteName } from "./palette";
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
  createScene?: (canvas: HTMLCanvasElement, cb: SceneCallbacks, palette: Palette) => SceneHandle;
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

function persistedPalette(): PaletteName {
  const api = vscode as { getState?: () => unknown };
  const s = api.getState?.() as { palette?: unknown } | undefined;
  return isPaletteName(s?.palette) ? s.palette : DEFAULT_PALETTE;
}

export default function GraphApp({ createScene }: Props) {
  const [conn, setConn] = useState<Conn>({ kind: "connecting" });
  const [model, setModel] = useState<SpaceModel | null>(null);
  const [staleAgeSec, setStaleAgeSec] = useState<number | null>(null);
  const [glFailed, setGlFailed] = useState(false);
  const morphRef = useRef<{ isDiff: boolean; removed: string[] }>({ isDiff: false, removed: [] });
  const [state, dispatch] = useReducer(graphReducer, undefined, initialGraphState);
  const [paletteName, setPaletteName] = useState<PaletteName>(persistedPalette);
  const [sceneEpoch, setSceneEpoch] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sceneRef = useRef<SceneHandle | null>(null);
  const factoryRef = useRef<Props["createScene"] | null>(createScene ?? null);
  const modelRef = useRef<SpaceModel | null>(null);
  const stateRef = useRef(state);
  const paletteRef = useRef(paletteName);
  const lastSatPickRef = useRef<{ id: string; at: number }>({ id: "", at: 0 });
  modelRef.current = model;
  stateRef.current = state;

  // Palette switch: scene colors are baked into GPU buffers, so dispose and
  // rebuild the scene (focus resets to overview — cheap and predictable).
  useEffect(() => {
    if (paletteRef.current === paletteName) return;
    paletteRef.current = paletteName;
    (vscode as { setState?: (s: unknown) => void }).setState?.({ palette: paletteName });
    sceneRef.current?.dispose();
    sceneRef.current = null;
    dispatch({ type: "reset" });
    setSceneEpoch((e) => e + 1);
  }, [paletteName]);

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
            const focus = stateRef.current.focus;
            const detail = stateRef.current.fileDetail;
            // Spec: clicking a lit thread's destination rides the thread — camera
            // travels the curve, refocus happens on arrival.
            const isTraceTarget =
              (focus.level === 2 || focus.level === 3) &&
              !!detail &&
              detail.edges.some((e) => e.otherFile === id);
            if (isTraceTarget && sceneRef.current) {
              sceneRef.current.rideToStar(id, () =>
                dispatch({ type: "pickStar", fileId: id, pkg: star.pkg })
              );
              return;
            }
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
          sceneRef.current = factoryRef.current!(canvas, callbacks, PALETTES[paletteRef.current]);
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
  }, [renderModel, sceneEpoch]);

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

  // Follow a connection out of the info card: ride the lit thread when there is
  // one (refocus on arrival), otherwise fly-to + refocus immediately.
  const goEdge = (fileId: string) => {
    const star = modelRef.current?.stars.find((s) => s.id === fileId);
    if (!star) return;
    const arrive = () => dispatch({ type: "pickStar", fileId, pkg: star.pkg });
    if (sceneRef.current) sceneRef.current.rideToStar(fileId, arrive);
    else arrive();
  };

  const topHub = model
    ? [...model.stars]
        .filter((s) => s.isHub)
        .sort((a, b) => b.inDeg + b.outDeg - (a.inDeg + a.outDeg))[0]
    : undefined;
  const strongestBundle = model?.bundles[0];
  const canRideBeam =
    state.focus.level >= 2
      ? (state.fileDetail?.edges.length ?? 0) > 0
      : strongestBundle !== undefined;

  const rideBeam = () => {
    if (state.focus.level >= 2) {
      const edges = stateRef.current.fileDetail?.edges ?? [];
      const target = edges.find((e) => e.crossPackage) ?? edges[0];
      if (target) goEdge(target.otherFile);
      return;
    }
    if (!strongestBundle) return;
    const { fromPkg, toPkg } = strongestBundle;
    const arrive = () => dispatch({ type: "pickPackage", pkg: toPkg });
    if (sceneRef.current) sceneRef.current.rideBeam(fromPkg, toPkg, arrive);
    else arrive();
  };

  const traceHub = () => {
    if (!topHub) return;
    dispatch({ type: "pickStar", fileId: topHub.id, pkg: topHub.pkg });
    sceneRef.current?.flyToStar(topHub.id, 300);
  };

  const pal = PALETTES[paletteName];
  const paletteVars = {
    background: pal.bgBot,
    "--ax-panel": pal.panel,
    "--ax-border": pal.panelBorder,
    "--ax-ink": pal.ink,
    "--ax-ink-dim": pal.inkDim,
    "--ax-accent": pal.accent,
    "--ax-accent-text": pal.accentText,
    "--ax-star": pal.star,
    "--ax-beacon": pal.beacon,
    "--ax-out": pal.out,
    "--ax-in": pal.inn,
    "--ax-k-imports": pal.kinds.Imports,
    "--ax-k-calls": pal.kinds.Calls,
    "--ax-k-inherits": pal.kinds.Inherits,
    "--ax-k-references": pal.kinds.References,
  } as React.CSSProperties;

  return (
    <div className="w-screen h-screen overflow-hidden relative" style={paletteVars}>
      {conn.kind === "connecting" && (
        <div className="flex items-center justify-center h-full text-xs uppercase tracking-[0.3em] text-[var(--ax-ink-dim)]">
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
          <div className="ax-vignette" />
          <Breadcrumb
            focus={state.focus}
            onPop={() => dispatch({ type: "pop" })}
            onReset={() => dispatch({ type: "reset" })}
          />
          {staleAgeSec !== null && (
            <div
              className="ax-glass absolute top-16 left-4 px-3 py-1.5 text-[10px] uppercase tracking-[0.15em]
                         text-[var(--ax-beacon)] opacity-80"
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
              symbolDetail={state.focus.level === 3 && state.focus.symbolId ? state.symbolDetail : null}
              onOpen={() => vscode.postMessage({ type: "openFile", path: focusedStar.id })}
              onDive={() => dispatch({ type: "dive" })}
              onGoEdge={goEdge}
            />
          )}
          <ViewPanel
            focusLevel={state.focus.level}
            canTraceHub={topHub !== undefined}
            canRideBeam={canRideBeam}
            palette={paletteName}
            onPalette={setPaletteName}
            onOverview={() => dispatch({ type: "reset" })}
            onTraceHub={traceHub}
            onRideBeam={rideBeam}
            onEnterFile={() => dispatch({ type: "dive" })}
          />
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
