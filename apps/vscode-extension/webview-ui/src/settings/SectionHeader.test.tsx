import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SectionHeader } from "./SectionHeader";

describe("SectionHeader", () => {
  it("renders title and description", () => {
    render(<SectionHeader title="Skills" description="Workspace skill catalog." />);
    expect(screen.getByRole("heading", { name: "Skills" })).toBeTruthy();
    expect(screen.getByText("Workspace skill catalog.")).toBeTruthy();
  });

  it("wires the search input when provided", () => {
    const onChange = vi.fn();
    render(
      <SectionHeader
        title="Skills"
        description="d"
        search={{ value: "", onChange, placeholder: "Type to search…" }}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("Type to search…"), { target: { value: "web" } });
    expect(onChange).toHaveBeenCalledWith("web");
  });
});
