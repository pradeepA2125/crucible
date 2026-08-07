import { fireEvent, render } from "@testing-library/react";
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { ToolTrack, resetToolTrackWidthCacheForTests } from "../components/shared/ToolTrack";
import type { ToolEventView } from "../types";

// jsdom has no layout engine. Pin the container width so packing is deterministic.
const CONTAINER_WIDTH = 300;

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => CONTAINER_WIDTH,
  });
});

// widthCache is module-level in ToolTrack.tsx (deliberately — it's meant to
// outlive one mount for the session). That also means it outlives one test.
// Reset before every test so a real-measurement test never sees a key some
// earlier test in this file already cached. The five measureWidths-based
// tests below never read or write the cache, so this is a no-op for them.
beforeEach(() => {
  resetToolTrackWidthCacheForTests();
});

function makeEvents(count: number): ToolEventView[] {
  return Array.from({ length: count }, (_, i) => ({
    id: i + 1,
    tool: "read_file",
    args: { path: `src/f${i}.ts` },
    source: "execution" as const,
    done: true,
    isError: false,
    output: "ok",
  }));
}

/** Every pill 80 wide => with SIDE_PAD 28 both sides and COL_GAP 14,
 *  avail = 300 - 56 = 244 => 80 + 14 + 80 + 14 + 80 = 268 > 244, so 2 per row. */
const fixedWidths = (events: ToolEventView[]) => events.map(() => 80);

describe("ToolTrack", () => {
  it("renders nothing for no events", () => {
    const { container } = render(<ToolTrack events={[]} measureWidths={fixedWidths} />);
    expect(container.querySelectorAll("[data-row]").length).toBe(0);
  });

  it("packs pills into rows that fit the container", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const rows = container.querySelectorAll("[data-row]");
    expect(rows.length).toBe(3);
    expect(rows[0].querySelectorAll("button").length).toBe(2);
    expect(rows[1].querySelectorAll("button").length).toBe(2);
    expect(rows[2].querySelectorAll("button").length).toBe(1);
  });

  it("alternates row direction so reading order follows the road", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const rows = container.querySelectorAll("[data-row]");
    expect(rows[0].getAttribute("data-dir")).toBe("r");
    expect(rows[1].getAttribute("data-dir")).toBe("l");
    expect(rows[2].getAttribute("data-dir")).toBe("r");
    // Odd rows must actually reverse visually, not just be labelled.
    expect(rows[1].className).toContain("flex-row-reverse");
    expect(rows[0].className).not.toContain("flex-row-reverse");
  });

  it("justifies full rows but never the last one", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const rows = container.querySelectorAll("[data-row]");
    expect(rows[0].getAttribute("data-fill")).toBe("true");
    expect(rows[1].getAttribute("data-fill")).toBe("true");
    expect(rows[2].getAttribute("data-fill")).toBe("false");
  });

  it("renders every event in call order", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    // DOM order is call order regardless of visual direction, so a screen
    // reader announces 1..n in sequence even though row 2 renders reversed.
    const ids = [...container.querySelectorAll("[data-row] button[data-event-id]")].map((el) =>
      el.getAttribute("data-event-id")
    );
    expect(ids).toEqual(["1", "2", "3", "4", "5"]);
  });

  it("mounts the detail panel immediately after the pill's own row", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    // Third pill lives on row 2 (rows hold 2, 2, 1).
    const pills = container.querySelectorAll("button[aria-expanded]");
    fireEvent.click(pills[2]);

    const panel = container.querySelector("[data-rowpanel]");
    expect(panel).not.toBeNull();

    const rows = [...container.querySelectorAll("[data-row]")];
    expect(panel!.previousElementSibling).toBe(rows[1]);
    expect(panel!.querySelector("[data-caret]")).not.toBeNull();
  });

  it("moves the panel rather than opening a second one", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const pills = container.querySelectorAll("button[aria-expanded]");

    fireEvent.click(pills[0]);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(1);

    fireEvent.click(pills[4]);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(1);
    const rows = [...container.querySelectorAll("[data-row]")];
    expect(container.querySelector("[data-rowpanel]")!.previousElementSibling).toBe(rows[2]);
  });

  it("collapses when the open pill is clicked again", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const pill = container.querySelectorAll("button[aria-expanded]")[0];

    fireEvent.click(pill);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(1);

    fireEvent.click(pill);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(0);
  });

  it("does not open a panel for a running call", () => {
    const events = makeEvents(2);
    events[1] = { ...events[1], done: false };
    const { container } = render(
      <ToolTrack events={events} measureWidths={fixedWidths} />
    );
    // The running pill carries no aria-expanded, so target it by position.
    const buttons = container.querySelectorAll("[data-row] button");
    fireEvent.click(buttons[1]);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(0);
  });

  it("renders a decorative rails layer with both caps", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const rails = container.querySelector("[data-rails]");
    expect(rails).not.toBeNull();
    expect(rails!.getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector("[data-rail-path]")).not.toBeNull();
    expect(container.querySelector('[data-cap="start"]')).not.toBeNull();
    expect(container.querySelector('[data-cap="end"]')).not.toBeNull();
  });

  it("draws no rails when there are no rows", () => {
    const { container } = render(<ToolTrack events={[]} measureWidths={fixedWidths} />);
    expect(container.querySelector("[data-rails]")).toBeNull();
  });
});

// The five tests above all inject `measureWidths`, so `needsProbe` is always
// false and the real DOM-measurement path — the probe render, the
// module-level cache, and the re-render it forces — never runs under them.
// These tests omit `measureWidths` so ToolTrack measures its own probe pills.
function makeNamedEvents(tools: string[]): ToolEventView[] {
  return tools.map((tool, i) => ({
    id: i + 1,
    tool,
    args: {},
    source: "execution" as const,
    done: true,
    isError: false,
    output: "ok",
  }));
}

/**
 * ToolPill's only text node for a done/ok pill is the tool name (icons are
 * svg, no text), so `textContent` is exactly `event.tool`. Deriving width
 * from it means different tools genuinely measure different widths here,
 * instead of every probed pill coincidentally landing on the same number.
 */
function stubMeasuredTextWidths() {
  return vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
    this: Element
  ) {
    const width = (this.textContent ?? "").length * 12 + 30;
    return {
      width,
      height: 20,
      top: 0,
      left: 0,
      right: width,
      bottom: 20,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect;
  });
}

/** Simulates a backgrounded VS Code webview tab: every child lays out at 0. */
function stubZeroWidths() {
  return vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    width: 0,
    height: 0,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

describe("ToolTrack — real measurement (no measureWidths)", () => {
  it("probes the DOM and packs rows from the measured widths", () => {
    const spy = stubMeasuredTextWidths();
    try {
      // ls=54, read_file=138, run_command=162; avail = 300 - 56 = 244.
      // ls + gap + read_file = 54 + 14 + 138 = 206 <= 244, fits one row.
      // + gap + run_command = 206 + 14 + 162 = 382 > 244, new row.
      const events = makeNamedEvents(["ls", "read_file", "run_command"]);
      const { container } = render(<ToolTrack events={events} />);
      const rows = container.querySelectorAll("[data-row]");
      expect(rows.length).toBe(2);
      expect(rows[0].querySelectorAll("button").length).toBe(2);
      expect(rows[1].querySelectorAll("button").length).toBe(1);
    } finally {
      spy.mockRestore();
    }
  });

  it("does not loop and does not poison the cache when every pill measures zero", () => {
    const zeroSpy = stubZeroWidths();
    const events = makeNamedEvents(["ls", "read_file", "run_command"]);

    // A hidden panel: every probe child measures 0. If ToolTrack cached that
    // (finding 2) or bumped its re-render trigger unconditionally on a
    // no-op measurement (the loop hazard the fix has to avoid), this render
    // would either pin permanently-crushed rows or throw synchronously —
    // React's own update-depth guard ("Maximum update depth exceeded") fires
    // well before the test would ever hang, so a regression here surfaces as
    // a thrown error, not a stuck test run.
    const first = render(<ToolTrack events={events} />);
    expect(first.container.querySelectorAll("[data-row]").length).toBe(0);
    first.unmount();
    zeroSpy.mockRestore();

    // Fresh mount, same tool/state keys, real widths this time. If the zero
    // pass above had cached 0 for these keys, this render would still see
    // those cached zeros (every pill "fits" in zero space) and collapse to a
    // single row instead of retrying the DOM. Getting the same 2/1 split as
    // the previous test proves the zero-measured keys were left out of the
    // cache rather than cached wrong.
    const realSpy = stubMeasuredTextWidths();
    try {
      const { container } = render(<ToolTrack events={events} />);
      const rows = container.querySelectorAll("[data-row]");
      expect(rows.length).toBe(2);
      expect(rows[0].querySelectorAll("button").length).toBe(2);
      expect(rows[1].querySelectorAll("button").length).toBe(1);
    } finally {
      realSpy.mockRestore();
    }
  });
});
