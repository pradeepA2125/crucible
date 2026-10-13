import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppearanceSection } from "./AppearanceSection";

describe("AppearanceSection", () => {
  it("shows the current appearance and accent as checked radios", () => {
    render(<AppearanceSection appearance="vscode" accent="blue" busy={false} send={vi.fn()} />);
    expect(screen.getByRole("radio", { name: /Match VS Code/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Blue/ })).toBeChecked();
  });

  it("posts settings/setAppearance with both values when either changes", () => {
    const send = vi.fn();
    render(<AppearanceSection appearance="vscode" accent="blue" busy={false} send={send} />);
    fireEvent.click(screen.getByRole("radio", { name: /Crucible Light/ }));
    expect(send).toHaveBeenCalledWith({ type: "settings/setAppearance", appearance: "light", accent: "blue" });
    fireEvent.click(screen.getByRole("radio", { name: /Ember/ }));
    expect(send).toHaveBeenLastCalledWith({ type: "settings/setAppearance", appearance: "vscode", accent: "ember" });
  });

  it("explains that high contrast overrides these choices", () => {
    render(<AppearanceSection appearance="dark" accent="violet" busy={false} send={vi.fn()} />);
    expect(screen.getByText(/High-contrast VS Code themes always win/)).toBeTruthy();
  });
});
