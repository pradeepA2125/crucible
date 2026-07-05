import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ProviderSection } from "./ProviderSection";
import type { SettingsState } from "../types";

const state: SettingsState = {
  provider: { backend: "gemini", model: "gemini-flash-latest" },
  runtime: null,
  mcp: { enabled: false, servers: [] },
  skills: [],
  envFlags: {},
  restartRequired: false,
};

describe("ProviderSection", () => {
  it("prefills from state and posts settings/setProvider on save", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("gemini-flash-latest");
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gemini-3-pro" } });
    fireEvent.click(screen.getByRole("button", { name: /Save & validate/ }));
    expect(send).toHaveBeenCalledWith({
      type: "settings/setProvider",
      backend: "gemini",
      model: "gemini-3-pro",
    });
  });

  it("includes apiKey only when typed for a non-local provider", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.change(screen.getByLabelText(/API key/), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByRole("button", { name: /Save & validate/ }));
    expect(send.mock.calls[0][0]).toMatchObject({ apiKey: "sk-test" });
  });
});
