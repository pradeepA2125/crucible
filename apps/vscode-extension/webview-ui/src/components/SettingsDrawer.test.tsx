import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SettingsDrawer } from "./SettingsDrawer";

const ALL_LABELS = [
  "Overview",
  "Provider",
  "MCP Servers",
  "Skills",
  "Instructions",
  "Policies & Memory",
  "Runtime",
];

describe("SettingsDrawer", () => {
  it("renders all seven sections when open", () => {
    render(<SettingsDrawer open onClose={() => {}} onSelect={() => {}} />);
    for (const label of ALL_LABELS) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeTruthy();
    }
  });

  it("calls onSelect with the section id when a row is clicked", () => {
    const onSelect = vi.fn();
    render(<SettingsDrawer open onClose={() => {}} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /Runtime/ }));
    expect(onSelect).toHaveBeenCalledWith("runtime");
  });

  it("closes on scrim click and on Escape", () => {
    const onClose = vi.fn();
    render(<SettingsDrawer open onClose={onClose} onSelect={() => {}} />);
    fireEvent.click(screen.getByTestId("drawer-scrim"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("renders nothing when closed", () => {
    const { container } = render(
      <SettingsDrawer open={false} onClose={() => {}} onSelect={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
