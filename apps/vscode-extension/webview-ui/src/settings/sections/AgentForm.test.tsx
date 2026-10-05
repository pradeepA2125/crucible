import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentForm } from "./AgentForm";
import type { AgentView } from "../types";

const TOOLS = ["read_file", "search_code", "edit", "mcp__github__*"];

function base(over: Partial<AgentView> = {}): AgentView {
  return {
    name: "repo", description: "Repo helper", tools: ["read_file"], disallowedTools: [],
    permission: "plan", declaredPermission: "plan", model: "inherit", maxTurns: 40,
    skills: [], source: "claude", path: "/ws/.claude/agents/repo.md", sha256: "bb",
    trust: "capped", active: true, warnings: [], shadowedBy: null, persona: "Be careful.",
    content: "x", ...over,
  };
}

function renderForm(props: Partial<Parameters<typeof AgentForm>[0]> = {}) {
  const onSave = vi.fn();
  render(<AgentForm mode="new" initial={null} availableTools={TOOLS} models={["gpt-5"]}
                    skillsEnabled={false} existingNames={new Set(["taken"])} busy={false}
                    onCancel={vi.fn()} onSave={onSave} {...props} />);
  return onSave;
}

describe("AgentForm", () => {
  it("new agent with all tools sends tools: null", () => {
    const onSave = renderForm();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "helper" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Helps" } });
    fireEvent.change(screen.getByLabelText("Role / persona"), { target: { value: "You help." } });
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave).toHaveBeenCalledWith("helper", {
      description: "Helps", persona: "You help.", tools: null, disallowedTools: [],
      permission: "default", model: "inherit", maxTurns: null, skills: [] });
  });

  it("an explicit tool list, and an empty one, are kept", () => {
    const onSave = renderForm();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "d" } });
    fireEvent.click(screen.getByLabelText("All tools"));
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave.mock.calls[0][1].tools).toEqual([]);
    fireEvent.click(screen.getByLabelText("Allow read_file"));
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave.mock.calls[1][1].tools).toEqual(["read_file"]);
  });

  it("invalid or reserved names disable save", () => {
    renderForm();
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "d" } });
    for (const bad of ["bad name", "trust", ""]) {
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: bad } });
      expect((screen.getByRole("button", { name: "Save agent" }) as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("warns when a new name replaces an existing .crucible file", () => {
    renderForm();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "taken" } });
    expect(screen.getByText(/replaces the existing \.crucible\/agents\/taken\.md/)).toBeTruthy();
  });

  it("duplicate pre-fills from the source and keeps its name", () => {
    const onSave = renderForm({ mode: "duplicate", initial: base() });
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("repo");
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave).toHaveBeenCalledWith("repo", {
      description: "Repo helper", persona: "Be careful.", tools: ["read_file"], disallowedTools: [],
      permission: "plan", model: "inherit", maxTurns: 40, skills: [] });
  });

  it("edit with a new name sends renameFrom", () => {
    const onSave = renderForm({ mode: "edit", initial: base({ source: "crucible" }) });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "repo2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave.mock.calls[0][0]).toBe("repo2");
    expect(onSave.mock.calls[0][1].renameFrom).toBe("repo");
  });

  it("offers inherit, the provider models, and the definition's own model", () => {
    renderForm({ mode: "duplicate", initial: base({ model: "custom-model" }) });
    const options = Array.from((screen.getByLabelText("Model") as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toEqual(["inherit", "gpt-5", "custom-model"]);
  });
});
