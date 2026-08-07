import { render } from "@testing-library/react";
import { describe, it, expect, beforeAll } from "vitest";
import { ToolTrack } from "../components/shared/ToolTrack";
import type { ToolEventView } from "../types";

// jsdom has no layout engine. Pin the container width so packing is deterministic.
const CONTAINER_WIDTH = 300;

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => CONTAINER_WIDTH,
  });
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
});
