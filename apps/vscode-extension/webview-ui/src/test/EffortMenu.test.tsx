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
    sendModelList({
      level: "high",
      support: { supported: ["high"], unsupported: { max: "x" } },
      note: "max unavailable here; using high.",
    });
    expect(screen.getByRole("button", { name: /no max/i })).toBeTruthy();
  });

  it("keeps the clamp note visible across a later modelList refresh carrying the same effort", () => {
    // Regression for the note-clearing bug: EffortMenu used to read a top-level
    // `effortNote` field and clear it on EVERY modelList message (e.g. the
    // plain listModels poll ModelMenu fires on mount), so the note vanished the
    // instant anything else refreshed the model list. The note now rides
    // inside `effort.note` (sourced from GET /v1/config), so a second message
    // carrying the identical effort object must still show it.
    render(<EffortMenu />);
    const effort = {
      level: "high",
      support: { supported: ["high"], unsupported: { max: "x" } },
      note: "max unavailable here; using high.",
    };
    sendModelList(effort);
    expect(screen.getByRole("button", { name: /no max/i })).toBeTruthy();

    // A second, unrelated modelList refresh (e.g. ModelMenu's mount-time poll)
    // carrying the SAME effort object must not clear the note.
    sendModelList(effort);
    expect(screen.getByRole("button", { name: /no max/i })).toBeTruthy();
  });
});
