import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentsSection } from "./AgentsSection";
import type { AgentView, SettingsState } from "../types";

const STATE: SettingsState = {
  provider: null, runtime: null, mcp: { enabled: true, servers: [] },
  skills: [], envFlags: {}, restartRequired: false,
};

function agent(over: Partial<AgentView>): AgentView {
  return {
    name: "a", description: "desc", tools: null, disallowedTools: [], permission: "default",
    declaredPermission: "default", model: "inherit", maxTurns: null, skills: [],
    source: "crucible", path: "/ws/.crucible/agents/a.md", sha256: "aa", trust: "trusted",
    active: true, warnings: [], shadowedBy: null, persona: "p", content: "---\nname: a\n---\np\n",
    ...over,
  };
}

const CATALOG = {
  agents: [
    agent({ name: "mine" }),
    agent({ name: "repo", source: "claude", path: "/ws/.claude/agents/repo.md", trust: "capped",
            permission: "default", declaredPermission: "acceptEdits", sha256: "bb",
            content: "RAW FILE TEXT", warnings: ["untrusted: acceptEdits runs as default"] }),
    agent({ name: "old", source: "claude", path: "/ws/.claude/agents/old.md", active: false,
            shadowedBy: "/ws/.crucible/agents/old.md" }),
    agent({ name: "general-purpose", source: "builtin", path: null, sha256: null, content: null }),
  ],
  skipped: [{ path: "/ws/.crucible/agents/broken.md", reason: "missing 'description' — skipped" }],
  availableTools: ["read_file", "edit"],
};

function deliver(data: unknown) {
  act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
}

function setup() {
  const send = vi.fn();
  render(<AgentsSection state={STATE} busy={false} send={send} />);
  deliver({ type: "settings/agents", catalog: CATALOG });
  return send;
}

describe("AgentsSection", () => {
  it("requests its data on mount", () => {
    const send = vi.fn();
    render(<AgentsSection state={STATE} busy={false} send={send} />);
    expect(send).toHaveBeenCalledWith({ type: "settings/listAgents" });
    expect(send).toHaveBeenCalledWith({ type: "settings/listModels" });
  });

  it("groups rows by source and shows clamps, shadowing and skipped files", () => {
    setup();
    expect(screen.getByText(".crucible/agents")).toBeTruthy();
    expect(screen.getByText("Built-in")).toBeTruthy();
    expect(screen.getByText("capped — declared acceptEdits")).toBeTruthy();
    expect(screen.getByText("overridden by /ws/.crucible/agents/old.md")).toBeTruthy();
    expect(screen.getAllByText("all tools").length).toBeGreaterThan(0);
    expect(screen.getByText(/broken\.md/)).toBeTruthy();
    expect(screen.getByText("missing 'description' — skipped")).toBeTruthy();
  });

  it("expands warnings", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "1 warning on repo" }));
    expect(screen.getByText("untrusted: acceptEdits runs as default")).toBeTruthy();
  });

  it("trust shows the exact file text and sends its sha256", () => {
    const send = setup();
    fireEvent.click(screen.getByRole("button", { name: "Trust repo" }));
    expect(screen.getByText("RAW FILE TEXT")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Trust this file" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/trustAgent",
      path: "/ws/.claude/agents/repo.md", sha256: "bb" });
  });

  it("delete asks twice and only on .crucible rows", () => {
    const send = setup();
    expect(screen.queryByRole("button", { name: "Delete repo" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Delete mine" }));
    expect(send).not.toHaveBeenCalledWith({ type: "settings/deleteAgent", name: "mine" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete mine" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/deleteAgent", name: "mine" });
  });

  it("opens a file row and says built-ins have none", () => {
    const send = setup();
    fireEvent.click(screen.getByRole("button", { name: "Open mine" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/openFile", path: "/ws/.crucible/agents/a.md" });
    expect(screen.getByText("built-in — no file")).toBeTruthy();
  });

  it("shows its own error without needing the settings snapshot", () => {
    setup();
    deliver({ type: "settings/agentsError", message: "the file changed since you reviewed it" });
    expect(screen.getByText("the file changed since you reviewed it")).toBeTruthy();
  });

  it("New agent opens the form and saves through settings/saveAgent", () => {
    const send = setup();
    fireEvent.click(screen.getByRole("button", { name: "New agent" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "fresh" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "d" } });
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(send).toHaveBeenCalledWith(expect.objectContaining({ type: "settings/saveAgent", name: "fresh" }));
  });
});
