import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { InstructionsSection } from "./InstructionsSection";

describe("InstructionsSection", () => {
  it("shows a loading state before the file arrives", () => {
    render(<InstructionsSection instructions={null} busy={false} send={vi.fn()} />);
    expect(screen.getByText(/Loading/)).toBeTruthy();
  });

  it("empty state offers Create, which saves an empty file", () => {
    const send = vi.fn();
    render(<InstructionsSection instructions={{ content: "", exists: false }} busy={false} send={send} />);
    expect(screen.getByText(/No AGENTS\.md yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Create AGENTS\.md/ }));
    expect(send).toHaveBeenCalledWith({ type: "settings/saveInstructions", content: "" });
  });

  it("editor enables Save only when dirty, then posts the content", () => {
    const send = vi.fn();
    render(<InstructionsSection instructions={{ content: "# a", exists: true }} busy={false} send={send} />);
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "# a\n- rule" } });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);
    expect(send).toHaveBeenCalledWith({ type: "settings/saveInstructions", content: "# a\n- rule" });
  });
});
