import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { OverviewSection } from "./OverviewSection";
import type { SettingsState } from "../types";

const state: SettingsState = {
  provider: { backend: "gemini", model: "gemini-flash-latest" },
  runtime: null,
  mcp: { enabled: true, servers: [{ name: "web", transport: "stdio", enabledInFile: true, state: "connected", detail: null, toolCount: 2, userEnabled: true }] },
  skills: [{ name: "code-review", description: "d", enabled: true }],
  envFlags: {},
  restartRequired: false,
};

describe("OverviewSection", () => {
  it("renders a card per section with counts, navigating on click", () => {
    const onNavigate = vi.fn();
    render(<OverviewSection state={state} onNavigate={onNavigate} />);
    expect(screen.getByText("MCP Servers")).toBeTruthy();
    expect(screen.getByText("1 server")).toBeTruthy();
    expect(screen.getByText("1 skill")).toBeTruthy();
    expect(screen.getByText(/gemini-flash-latest/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Instructions/ }));
    expect(onNavigate).toHaveBeenCalledWith("instructions");
  });
});
