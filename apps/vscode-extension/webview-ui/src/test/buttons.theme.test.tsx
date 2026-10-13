import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BtnPrimary } from "../components/shared/buttons";

describe("BtnPrimary theming", () => {
  it("takes its text colour from --on-accent, not a hardcoded white", () => {
    render(<BtnPrimary onClick={() => {}}>Save</BtnPrimary>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button.className).not.toMatch(/\btext-white\b/);
    expect(button.style.color).toBe("var(--on-accent)");
  });
});
