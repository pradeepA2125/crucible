import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EffortMenu } from "../components/EffortMenu";

// window.dispatchEvent is a raw native dispatch (unlike RTL's fireEvent, which
// wraps itself in act()) — without act() here, the resulting setState is
// batched into a microtask and the assertion below races it. Same pattern as
// assembly.test.tsx's window "message" dispatches.
function sendModelList(effort: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data: { type: "modelList", effort } }));
  });
}

describe("EffortMenu", () => {
  it("shows the current rung on the chip", () => {
    render(<EffortMenu />);
    sendModelList({ level: "high", support: { supported: ["high"], unsupported: {} } });
    expect(screen.getByRole("button", { name: /high/i })).toBeTruthy();
  });

  it("disables an unsupported rung and shows why", () => {
    render(<EffortMenu />);
    sendModelList({
      level: "high",
      support: { supported: ["off", "low", "high"], unsupported: { max: "tops out at high" } },
    });
    fireEvent.click(screen.getByRole("button", { name: /high/i }));
    const max = screen.getByRole("menuitem", { name: /max/i });
    expect(max.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/tops out at high/i)).toBeTruthy();
  });

  it("marks an unverified rung selectable but flagged", () => {
    render(<EffortMenu />);
    sendModelList({ level: null, support: { supported: [], unsupported: {} } });
    fireEvent.click(screen.getByRole("button", { name: /effort/i }));
    const medium = screen.getByRole("menuitem", { name: /medium/i });
    expect(medium.hasAttribute("disabled")).toBe(false);
    expect(screen.getAllByText(/unverified/i).length).toBeGreaterThan(0);
  });

  it("surfaces a clamp note on the chip", () => {
    render(<EffortMenu />);
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: {
            type: "modelList",
            effort: { level: "high", support: { supported: ["high"], unsupported: { max: "x" } } },
            effortNote: "max unavailable here; using high.",
          },
        })
      );
    });
    expect(screen.getByRole("button", { name: /no max/i })).toBeTruthy();
  });
});
