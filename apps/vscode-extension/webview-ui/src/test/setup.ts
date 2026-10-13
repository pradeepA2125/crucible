import "@testing-library/jest-dom";
import { vi } from "vitest";

vi.stubGlobal("acquireVsCodeApi", () => ({ postMessage: vi.fn() }));

// jsdom does not implement ResizeObserver (used by the graph canvas).
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
