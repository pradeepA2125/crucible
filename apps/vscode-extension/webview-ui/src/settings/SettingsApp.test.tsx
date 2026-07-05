import { render, screen, fireEvent, act, within } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import SettingsApp from "./SettingsApp";
import { vscode } from "./vscodeApi";
import type { SettingsState } from "./types";

/** The Overview grid also renders section buttons, so scope nav clicks to the rail. */
function navClick(name: RegExp) {
  const rail = screen.getByRole("navigation", { name: "Settings sections" });
  fireEvent.click(within(rail).getByRole("button", { name }));
}

vi.mock("./vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const state: SettingsState = {
  provider: { backend: "gemini", model: "gemini-flash-latest" },
  runtime: null,
  mcp: { enabled: true, servers: [] },
  skills: [{ name: "s1", description: "d", enabled: true }],
  envFlags: {},
  restartRequired: true,
};

function deliver(data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data }));
  });
}

describe("SettingsApp shell", () => {
  beforeEach(() => vi.clearAllMocks());

  it("posts settings/load on mount, lands on Overview, and navigates", () => {
    render(<SettingsApp />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/load" });
    deliver({ type: "settings/state", state });
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
    navClick(/Provider/);
    expect(screen.getByRole("heading", { name: "Provider" })).toBeTruthy();
  });

  it("requests instructions on first visit to the Instructions section", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/state", state });
    navClick(/Instructions/);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/loadInstructions" });
    deliver({ type: "settings/instructions", content: "# rules", exists: true });
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("# rules");
  });

  it("shows the global restart banner in every section", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/state", state });
    expect(screen.getByText(/require a backend restart/)).toBeTruthy();
    navClick(/Skills/);
    expect(screen.getByText(/require a backend restart/)).toBeTruthy();
  });
});
