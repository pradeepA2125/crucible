import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { McpSection } from "./McpSection";
import type { SettingsState } from "../types";

function makeState(overrides: Partial<SettingsState["mcp"]> = {}): SettingsState {
  return {
    provider: null,
    runtime: null,
    mcp: {
      enabled: true,
      servers: [
        { name: "web", transport: "stdio", enabledInFile: true, state: "connected", detail: null, toolCount: 1, userEnabled: true,
          tools: [{ name: "a", description: "does a", enabled: true }, { name: "b", description: "does b", enabled: false }],
          enabledTools: null, disabledTools: ["b"] },
        { name: "gh", transport: "http", enabledInFile: true, state: "failed", detail: "missing GITHUB_PAT", toolCount: 0, userEnabled: true,
          tools: [], enabledTools: null, disabledTools: [] },
      ],
      ...overrides,
    },
    skills: [],
    envFlags: {},
    restartRequired: false,
  };
}

describe("McpSection", () => {
  it("renders server rows and toggles via the switch", () => {
    const send = vi.fn();
    render(<McpSection state={makeState()} busy={false} send={send} />);
    expect(screen.getByText("web")).toBeTruthy();
    expect(screen.getByText("missing GITHUB_PAT")).toBeTruthy();
    fireEvent.click(screen.getByRole("switch", { name: "Enable web" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/mcpToggle", name: "web", enabled: false });
  });

  it("filters servers via search", () => {
    render(<McpSection state={makeState()} busy={false} send={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("Type to search…"), { target: { value: "gh" } });
    expect(screen.queryByText("web")).toBeNull();
    expect(screen.getByText("gh")).toBeTruthy();
  });

  it("posts settings/mcpUpsert from the add-server form", () => {
    const send = vi.fn();
    render(<McpSection state={makeState()} busy={false} send={send} />);
    fireEvent.change(screen.getByPlaceholderText("name"), { target: { value: "docs" } });
    fireEvent.change(screen.getByPlaceholderText(/command line/), { target: { value: "uv run docs.py" } });
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(send).toHaveBeenCalledWith({
      type: "settings/mcpUpsert",
      name: "docs",
      entry: { command: "uv", args: ["run", "docs.py"], enabled: true },
    });
  });

  it("lists every tool behind a disclosure and toggles one", () => {
    const send = vi.fn();
    render(<McpSection state={makeState()} busy={false} send={send} />);
    fireEvent.click(screen.getByRole("button", { name: "Tools for web: 1 of 2 enabled" }));
    const a = screen.getByRole("checkbox", { name: "a" }) as HTMLInputElement;
    const b = screen.getByRole("checkbox", { name: "b" }) as HTMLInputElement;
    expect(a.checked).toBe(true);
    expect(b.checked).toBe(false);
    fireEvent.click(b);
    expect(send).toHaveBeenCalledWith({
      type: "settings/mcpTools", name: "web", enabledTools: null, disabledTools: [] });
  });

  it("offers no tool list for a server that is not connected", () => {
    render(<McpSection state={makeState()} busy={false} send={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /^Tools for gh/ })).toBeNull();
  });

  it("disables tool checkboxes while busy", () => {
    render(<McpSection state={makeState()} busy={true} send={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Tools for web: 1 of 2 enabled" }));
    expect((screen.getByRole("checkbox", { name: "a" }) as HTMLInputElement).disabled).toBe(true);
  });
});

