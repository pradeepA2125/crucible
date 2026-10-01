import { describe, expect, it } from "vitest";
import { inputAvailability } from "./inputAvailability";
import type { LiveGateView } from "./types";

const base = { inputEnabled: true, liveStatus: null, workbar: null,
               liveGates: [] as LiveGateView[], turnActive: false } as const;

describe("inputAvailability — controller precedence", () => {
  it("an edit gate anywhere in the list disables input", () => {
    const r = inputAvailability({
      ...base, liveGates: [
        { gateId: "a", kind: "command", taskId: "t", payload: {} },
        { gateId: "b", kind: "edit", taskId: "t", payload: {} },
      ] });
    expect(r.placeholder).toMatch(/decision on the card/i);
  });

  it("edit gate → disabled, decision placeholder", () => {
    const r = inputAvailability({
      ...base, liveGates: [{ gateId: "g", kind: "edit", taskId: "t", payload: {} }] });
    expect(r.disabled).toBe(true);
    expect(r.placeholder).toMatch(/decision on the card/i);
  });

  it("mode gate → disabled, choose-how placeholder", () => {
    const r = inputAvailability({
      ...base, liveGates: [{ gateId: "g", kind: "mode", taskId: "t", payload: {} }] });
    expect(r.disabled).toBe(true);
    expect(r.placeholder).toMatch(/choose how to proceed/i);
  });

  it("turn_active (no gate) → disabled, working placeholder, Stop shown", () => {
    const r = inputAvailability({ ...base, turnActive: true });
    expect(r.disabled).toBe(true);
    expect(r.placeholder).toMatch(/working/i);
    expect(r.showStop).toBe(true);
  });

  it("no gate, no turn, no task → enabled", () => {
    const r = inputAvailability(base);
    expect(r.disabled).toBe(false);
  });

  it("flag-off regression: task gate still disables (existing behavior)", () => {
    const r = inputAvailability({ ...base, liveStatus: "AWAITING_STEP_REVIEW" });
    expect(r.disabled).toBe(true);
    expect(r.placeholder).toMatch(/decision on the card/i);
  });
});
