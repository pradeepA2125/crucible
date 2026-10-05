import { describe, expect, it } from "vitest";
import { MAIN_COLOR, MEMBER_PALETTE, SYSTEM_COLOR, identityFor } from "../teamIdentity";

describe("member identity", () => {
  const roster = ["review", "impl", "reader"];
  it("assigns palette hues by roster order", () => {
    expect(identityFor("review", roster).color).toBe(MEMBER_PALETTE[0]);
    expect(identityFor("impl", roster).color).toBe(MEMBER_PALETTE[1]);
  });
  it("uses one letter, two on a collision", () => {
    expect(identityFor("impl", roster).initial).toBe("I");
    expect(identityFor("review", roster).initial).toBe("Re");
    expect(identityFor("reader", roster).initial).toBe("Ra");
  });
  it("main and system are fixed", () => {
    expect(identityFor("main", roster)).toEqual({ initial: "✦", color: MAIN_COLOR, kind: "main" });
    expect(identityFor("system", roster)).toMatchObject({ color: SYSTEM_COLOR, kind: "system" });
  });
  it("an unknown label is grey with its initial", () => {
    expect(identityFor("ghost", roster)).toEqual({ initial: "G", color: SYSTEM_COLOR, kind: "member" });
  });
  it("an Avatar puts no text into its surroundings", async () => {
    const { render } = await import("@testing-library/react");
    const { Avatar } = await import("../components/teams/Avatar");
    const { container } = render(<span><Avatar label="review" roster={roster} />review</span>);
    expect(container.textContent).toBe("review");
    expect(container.querySelector("[data-initial]")?.getAttribute("data-initial")).toBe("Re");
  });
  it("wraps the palette past six members", () => {
    const big = ["a1", "b2", "c3", "d4", "e5", "f6", "g7"];
    expect(identityFor("g7", big).color).toBe(MEMBER_PALETTE[0]);
  });
});
