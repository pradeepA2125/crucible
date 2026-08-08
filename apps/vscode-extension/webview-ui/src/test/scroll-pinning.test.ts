import { describe, it, expect } from "vitest";
import { isPinnedToBottom, scrollOffsetToReveal } from "../components/shared/scroll-pinning";

describe("isPinnedToBottom", () => {
  it("is pinned when the box is not scrollable at all", () => {
    // A short list that fits: nothing has scrolled, so following the stream is
    // still the right behaviour.
    expect(isPinnedToBottom({ scrollTop: 0, scrollHeight: 80, clientHeight: 160 })).toBe(true);
  });

  it("is pinned when scrolled to the exact bottom", () => {
    expect(isPinnedToBottom({ scrollTop: 240, scrollHeight: 400, clientHeight: 160 })).toBe(true);
  });

  it("stays pinned within the slack threshold", () => {
    // 400 - 380 - 160 = -140 ... use a case just inside the default 24px slack:
    expect(isPinnedToBottom({ scrollTop: 220, scrollHeight: 400, clientHeight: 160 })).toBe(true);
  });

  it("is not pinned once the reader has scrolled up past the slack", () => {
    // 400 - 100 - 160 = 140px from the bottom — the reader is looking at history.
    expect(isPinnedToBottom({ scrollTop: 100, scrollHeight: 400, clientHeight: 160 })).toBe(false);
  });

  it("honours a custom threshold", () => {
    const m = { scrollTop: 200, scrollHeight: 400, clientHeight: 160 };
    expect(isPinnedToBottom(m, 40)).toBe(true);
    expect(isPinnedToBottom(m, 8)).toBe(false);
  });
});

describe("scrollOffsetToReveal", () => {
  const view = { scrollTop: 100, clientHeight: 200 };

  it("returns null when the item is already fully visible", () => {
    expect(scrollOffsetToReveal(view, { offsetTop: 120, offsetHeight: 20 })).toBeNull();
  });

  it("scrolls up so an item above the fold sits at the top edge", () => {
    expect(scrollOffsetToReveal(view, { offsetTop: 40, offsetHeight: 20 })).toBe(40);
  });

  it("scrolls down the minimum needed to reveal an item below the fold", () => {
    // item bottom 340, viewport bottom 300 => scrollTop must become 340 - 200 = 140
    expect(scrollOffsetToReveal(view, { offsetTop: 320, offsetHeight: 20 })).toBe(140);
  });

  it("returns null when the container has no layout yet", () => {
    // jsdom reports zeroes; never emit a bogus scroll from an unmeasured box.
    expect(scrollOffsetToReveal({ scrollTop: 0, clientHeight: 0 }, { offsetTop: 0, offsetHeight: 0 }))
      .toBeNull();
  });
});
