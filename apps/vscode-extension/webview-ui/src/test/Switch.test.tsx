import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { Switch } from "../components/shared/Switch";

describe("Switch", () => {
  it("reflects checked state via aria and toggles on click", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} label="Enable web" />);
    const sw = screen.getByRole("switch", { name: "Enable web" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("does not fire when disabled", () => {
    const onChange = vi.fn();
    render(<Switch checked disabled onChange={onChange} label="x" />);
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
  });
});
