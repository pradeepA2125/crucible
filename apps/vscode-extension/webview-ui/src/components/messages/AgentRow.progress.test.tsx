import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentRow } from "./AgentRow";

// The shared Avatar is the only element with h-5 + rounded-md.
const AVATAR_SELECTOR = ".h-5.rounded-md";

describe("progress notes", () => {
  it("render as body-size markdown prose, not muted italic", () => {
    render(<AgentRow content="Found it: the **payload** still has images." progress />);
    const strong = screen.getByText("payload");
    expect(strong.tagName).toBe("STRONG");
    expect(strong.closest(".italic")).toBeNull();
    expect(strong.closest(".text-text-3")).toBeNull();
  });

  it("renders no avatar or spacer", () => {
    const { container } = render(<AgentRow content="first" progress />);
    expect(container.querySelector("[data-testid='avatar-spacer']")).toBeNull();
    expect(container.querySelector(AVATAR_SELECTOR)).toBeNull();
  });
});
