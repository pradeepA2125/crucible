import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ChatSettingsOverlay } from "./ChatSettingsOverlay";

// Isolate the overlay chrome from the real settings data flow — the embedded
// SettingsApp is exercised by its own tests. We only care that the overlay mounts
// it at the right section and that ✕/Escape/backdrop close it.
vi.mock("../settings/SettingsApp", () => ({
  default: ({ initialSection }: { initialSection?: string }) => (
    <div data-testid="settings-app">section:{initialSection ?? "overview"}</div>
  ),
}));

describe("ChatSettingsOverlay", () => {
  it("mounts the embedded SettingsApp opened at the given section", () => {
    render(<ChatSettingsOverlay section="provider" onClose={() => {}} />);
    expect(screen.getByTestId("settings-app").textContent).toContain("provider");
  });

  it("closes on the ✕ button", () => {
    const onClose = vi.fn();
    render(<ChatSettingsOverlay section="overview" onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /close settings/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<ChatSettingsOverlay section="overview" onClose={onClose} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes when the backdrop itself is clicked", () => {
    const onClose = vi.fn();
    render(<ChatSettingsOverlay section="overview" onClose={onClose} />);
    fireEvent.click(screen.getByTestId("overlay-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not close when the card interior is clicked", () => {
    const onClose = vi.fn();
    render(<ChatSettingsOverlay section="overview" onClose={onClose} />);
    fireEvent.click(screen.getByTestId("settings-app"));
    expect(onClose).not.toHaveBeenCalled();
  });
});
