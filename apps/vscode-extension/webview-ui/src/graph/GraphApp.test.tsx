import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import GraphApp from "./GraphApp";
import type { SceneHandle } from "./types";

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

  it("hands the model to the scene when space arrives", () => {
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
    expect(scene.setSpace).toHaveBeenCalledOnce();
  });
});
