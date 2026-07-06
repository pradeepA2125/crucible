import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import GraphApp from "./GraphApp";
import type { SceneCallbacks, SceneHandle, SpaceModel } from "./types";

const modelWithOneStar: SpaceModel = {
  workspaceRoot: "/ws",
  generatedAtMs: 1,
  packages: [{ id: "apps/web", fileCount: 1, dirs: ["apps/web/src"] }],
  stars: [
    {
      id: "apps/web/src/a.ts",
      pkg: "apps/web",
      dir: "apps/web/src",
      symbolCount: 2,
      inDeg: 1,
      outDeg: 1,
      kindMix: {},
      isEntry: false,
      isHub: false,
    },
  ],
  bundles: [],
  intraBundles: [],
  links: [],
};

const postMessage = vi.fn();
vi.mock("./vscodeApi", () => ({ vscode: { postMessage: (m: unknown) => postMessage(m) } }));

function fakeScene(): SceneHandle {
  return {
    setSpace: vi.fn(),
    morph: vi.fn(),
    setFocus: vi.fn(),
    showFileTrace: vi.fn(),
    showSatellites: vi.fn(),
    showSymbolTrace: vi.fn(),
    clearOverlays: vi.fn(),
    setLayers: vi.fn(),
    flyToStar: vi.fn(),
    framePackage: vi.fn(),
    resetCamera: vi.fn(),
    dispose: vi.fn(),
  };
}

function hostPost(msg: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data: msg }));
  });
}

describe("GraphApp shell", () => {
  beforeEach(() => postMessage.mockClear());

  it("posts ready on mount and shows connecting state", () => {
    render(<GraphApp createScene={() => fakeScene()} />);
    expect(postMessage).toHaveBeenCalledWith({ type: "ready" });
    expect(screen.getByText(/mapping the space/i)).toBeTruthy();
  });

  it("renders EmptyState on noSnapshot with a Build index CTA", () => {
    render(<GraphApp createScene={() => fakeScene()} />);
    hostPost({ type: "noSnapshot", reason: "missing", message: "no file", building: false });
    fireEvent.click(screen.getByRole("button", { name: /build index/i }));
    expect(postMessage).toHaveBeenCalledWith({ type: "buildIndex" });
  });

  it("hands the model to the scene when space arrives", async () => {
    const scene = fakeScene();
    render(<GraphApp createScene={() => scene} />);
    hostPost({
      type: "space",
      staleAgeSec: null,
      model: {
        workspaceRoot: "/ws",
        generatedAtMs: 1,
        packages: [],
        stars: [],
        bundles: [],
        intraBundles: [],
        links: [],
      },
    });
    // Layout is async (worker with sync fallback) — wait for the scene handoff.
    await waitFor(() => expect(scene.setSpace).toHaveBeenCalledOnce());
  });

  it("clicking a star requests fileDetail and shows the info card", async () => {
    const scene = fakeScene();
    let cb: SceneCallbacks | null = null;
    render(
      <GraphApp
        createScene={(_c, callbacks) => {
          cb = callbacks;
          return scene;
        }}
      />
    );
    hostPost({ type: "space", staleAgeSec: null, model: modelWithOneStar });
    await waitFor(() => expect(scene.setSpace).toHaveBeenCalled());
    act(() => cb!.onPickStar("apps/web/src/a.ts"));
    expect(postMessage).toHaveBeenCalledWith({ type: "fileDetail", fileId: "apps/web/src/a.ts" });
    expect(screen.getByRole("button", { name: /open in editor/i })).toBeTruthy();
  });

  it("Escape pops focus back to the package level", async () => {
    const scene = fakeScene();
    let cb: SceneCallbacks | null = null;
    render(
      <GraphApp
        createScene={(_c, callbacks) => {
          cb = callbacks;
          return scene;
        }}
      />
    );
    hostPost({ type: "space", staleAgeSec: null, model: modelWithOneStar });
    await waitFor(() => expect(scene.setSpace).toHaveBeenCalled());
    act(() => cb!.onPickStar("apps/web/src/a.ts"));
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(scene.setFocus).toHaveBeenLastCalledWith({ level: 1, pkg: "apps/web" });
  });
});
