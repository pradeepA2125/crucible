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

// jsdom has no layout engine: every element reports clientWidth 0 and a
// zero-sized getBoundingClientRect. ToolTrack derives its row packing from
// exactly those two measurements, so without a stub it renders no rows at all
// and every test that merely touches a tool pill fails for the wrong reason.
// Give elements a plausible chat-panel width, and a width proportional to their
// text so distinct pills measure differently. ToolTrack's own test file
// overrides both with stricter deterministic values.
Object.defineProperty(HTMLElement.prototype, "clientWidth", {
  configurable: true,
  get: () => 600,
});

Element.prototype.getBoundingClientRect = function (this: Element): DOMRect {
  const width = (this.textContent?.length ?? 0) * 7 + 30;
  const height = 20;
  return {
    x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height,
    toJSON: () => ({}),
  } as DOMRect;
};
