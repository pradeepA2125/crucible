import "@testing-library/jest-dom";
import { vi } from "vitest";

vi.stubGlobal("acquireVsCodeApi", () => ({ postMessage: vi.fn() }));

// jsdom does not implement ResizeObserver. ToolTrack observes its container to
// re-pack rows; tests drive width through a stubbed clientWidth instead.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
