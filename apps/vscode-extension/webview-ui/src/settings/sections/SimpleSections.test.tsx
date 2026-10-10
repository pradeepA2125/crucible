import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SkillsSection } from "./SkillsSection";
import { PoliciesSection } from "./PoliciesSection";
import { RuntimeSection } from "./RuntimeSection";
import type { SettingsState } from "../types";

const state: SettingsState = {
  provider: null,
  runtime: { releaseTag: "v0.3.0", components: { agentd: "0.3.0", indexer: "0.3.0" } },
  mcp: { enabled: false, servers: [] },
  skills: [
    { name: "brainstorming", description: "Explore ideas", enabled: true },
    { name: "systematic-debugging", description: "Debug carefully", enabled: false },
  ],
  envFlags: { "crucible.policy.shell": "ask" },
  restartRequired: false,
};

describe("SkillsSection", () => {
  it("toggles a skill and filters", () => {
    const send = vi.fn();
    render(<SkillsSection state={state} busy={false} send={send} />);
    fireEvent.click(screen.getByRole("switch", { name: "Enable brainstorming" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/skillToggle", name: "brainstorming", enabled: false });
    fireEvent.change(screen.getByPlaceholderText("Type to search…"), { target: { value: "debug" } });
    expect(screen.queryByText("brainstorming")).toBeNull();
  });
});

describe("PoliciesSection", () => {
  it("posts settings/setEnvFlag on change", () => {
    const send = vi.fn();
    render(<PoliciesSection state={state} busy={false} send={send} />);
    fireEvent.change(screen.getByLabelText("Shell command policy"), { target: { value: "allow_all" } });
    expect(send).toHaveBeenCalledWith({ type: "settings/setEnvFlag", key: "crucible.policy.shell", value: "allow_all" });
  });
});

describe("RuntimeSection", () => {
  it("shows versions and posts restart", () => {
    const send = vi.fn();
    render(<RuntimeSection state={state} busy={false} send={send} />);
    expect(screen.getByText(/v0\.3\.0/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Restart backend/ }));
    expect(send).toHaveBeenCalledWith({ type: "settings/restartBackend" });
  });
});

describe("Native web search setting", () => {
  it("defaults true, shows persisted false, and posts both choices", () => {
    const send = vi.fn();
    const { rerender } = render(<PoliciesSection state={state} busy={false} send={send} />);
    const select = screen.getByLabelText("Native web search") as HTMLSelectElement;
    expect(select.value).toBe("true");
    fireEvent.change(select, { target: { value: "false" } });
    expect(send).toHaveBeenLastCalledWith({ type: "settings/setEnvFlag", key: "crucible.webSearch.enabled", value: "false" });
    rerender(<PoliciesSection state={{ ...state, envFlags: { "crucible.webSearch.enabled": "false" } }} busy={false} send={send} />);
    expect(select.value).toBe("false");
    fireEvent.change(select, { target: { value: "true" } });
    expect(send).toHaveBeenLastCalledWith({ type: "settings/setEnvFlag", key: "crucible.webSearch.enabled", value: "true" });
  });
});

describe("Native web fetch setting", () => {
  it("defaults true, shows persisted false, and posts both choices", () => {
    const send = vi.fn();
    const { rerender } = render(<PoliciesSection state={state} busy={false} send={send} />);
    const select = screen.getByLabelText("Native web fetch") as HTMLSelectElement;
    expect(select.value).toBe("true");
    fireEvent.change(select, { target: { value: "false" } });
    expect(send).toHaveBeenLastCalledWith({ type: "settings/setEnvFlag", key: "crucible.webFetch.enabled", value: "false" });
    rerender(<PoliciesSection state={{ ...state, envFlags: { "crucible.webFetch.enabled": "false" } }} busy={false} send={send} />);
    expect(select.value).toBe("false");
    fireEvent.change(select, { target: { value: "true" } });
    expect(send).toHaveBeenLastCalledWith({ type: "settings/setEnvFlag", key: "crucible.webFetch.enabled", value: "true" });
  });
});
