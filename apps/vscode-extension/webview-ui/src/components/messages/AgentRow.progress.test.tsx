import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentRow } from "./AgentRow";

describe("progress notes", () => {
  it("render as body-size markdown prose, not muted italic", () => {
    render(<AgentRow content="Found it: the **payload** still has images." progress />);
    const strong = screen.getByText("payload");
    expect(strong.tagName).toBe("STRONG");
    expect(strong.closest(".italic")).toBeNull();
    expect(strong.closest(".text-text-3")).toBeNull();
  });

  it("can omit the avatar to continue the previous agent row", () => {
    const { container } = render(<AgentRow content="next" progress showAvatar={false} />);
    expect(container.querySelector("[data-testid='avatar-spacer']")).not.toBeNull();
  });
});
