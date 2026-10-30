import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SandboxConfig, SettingsInMsg, SettingsState } from "../types";
import { SandboxSection } from "./SandboxSection";

const config: SandboxConfig = {
  status: { enabled: true, available: true, reason: null },
  defaultHosts: ["registry.npmjs.org", "github.com"],
  hosts: ["example.com"],
  folders: ["/data/cache"],
};

function setup(initial: SandboxConfig | { error: string } = config) {
  const send = vi.fn<(msg: SettingsInMsg) => void>();
  render(<SandboxSection state={{} as SettingsState} busy={false} send={send} />);
  const data = "error" in initial
    ? { type: "settings/sandboxError", message: initial.error }
    : { type: "settings/sandboxState", config: initial };
  act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
  return send;
}

describe("SandboxSection", () => {
  it("asks for its own state on mount", () => {
    expect(setup()).toHaveBeenCalledWith({ type: "settings/sandbox" });
    expect(screen.getByRole("heading", { name: "Sandbox" })).toBeInTheDocument();
  });

  it("posts the switch", () => {
    const send = setup();
    const toggle = screen.getByRole("switch", { name: "Run agent commands in a sandbox" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);
    expect(send).toHaveBeenCalledWith({ type: "settings/setSandboxEnabled", enabled: false });
  });

  it.each([
    [{ enabled: true, available: true, reason: null }, "On"],
    [{ enabled: false, available: true, reason: null }, "Off"],
    [{ enabled: true, available: false, reason: "bubblewrap is not installed" },
      "Unavailable: bubblewrap is not installed — commands run unprotected"],
    [{ enabled: true, available: false, reason: null }, "Checking…"],
  ])("states the status %#", (status, text) => {
    setup({ ...config, status });
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it("shows default hosts read-only and removes a remembered one", () => {
    const send = setup();
    expect(screen.getByText("Allowed hosts for this workspace")).toBeInTheDocument();
    expect(screen.getByText("registry.npmjs.org")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove registry.npmjs.org" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove example.com" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/setSandboxHosts", hosts: [] });
  });

  it("adds a host and a folder", () => {
    const send = setup();
    fireEvent.change(screen.getByRole("textbox", { name: "Add a host" }), { target: { value: " pypi.example.com " } });
    fireEvent.click(screen.getByRole("button", { name: "Add host" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/setSandboxHosts", hosts: ["example.com", "pypi.example.com"] });
    expect(screen.getByText("Extra writable folders")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Add a folder" }), { target: { value: "/data/out" } });
    fireEvent.click(screen.getByRole("button", { name: "Add folder" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/setSandboxFolders", folders: ["/data/cache", "/data/out"] });
  });

  it("removes a folder", () => {
    const send = setup();
    fireEvent.click(screen.getByRole("button", { name: "Remove /data/cache" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/setSandboxFolders", folders: [] });
  });

  it("keeps an error in the section", () => {
    setup({ error: "not a bare host" });
    expect(screen.getByRole("alert")).toHaveTextContent("not a bare host");
  });
});
