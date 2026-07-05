import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { NavRail } from "./NavRail";

describe("NavRail", () => {
  it("lists all seven sections and fires onSelect", () => {
    const onSelect = vi.fn();
    render(<NavRail active="overview" counts={{ skills: 14, mcp: 2 }} onSelect={onSelect} />);
    for (const label of ["Overview", "Provider", "MCP Servers", "Skills", "Instructions", "Policies & Memory", "Runtime"]) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: /Skills/ }));
    expect(onSelect).toHaveBeenCalledWith("skills");
  });

  it("shows count badges and marks the active item", () => {
    render(<NavRail active="skills" counts={{ skills: 14 }} onSelect={() => {}} />);
    expect(screen.getByText("14")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Skills/ }).getAttribute("aria-current")).toBe("page");
  });
});
