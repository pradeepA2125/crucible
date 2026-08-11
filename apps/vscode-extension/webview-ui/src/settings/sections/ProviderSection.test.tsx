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
      // state.provider has no contextWindow, so the field prefilled from the
      // starter table's "gemini" entry (1,000,000) and that is what gets sent.
      contextWindow: 1_000_000,
    });
  });

  it("includes apiKey only when typed for a non-local provider", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.change(screen.getByLabelText(/API key/), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByRole("button", { name: /Save & validate/ }));
    expect(send.mock.calls[0][0]).toMatchObject({ apiKey: "sk-test" });
  });

  it("Clear key posts a delete for the provider currently selected in the dropdown", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    // Switch the dropdown WITHOUT saving — clearing must target what the key field
    // is labeled with (openai_compatible), not the still-active saved provider.
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "openai_compatible" } });
    fireEvent.click(screen.getByRole("button", { name: /Clear key/ }));
    expect(send).toHaveBeenCalledWith({
      type: "settings/clearProviderKey",
      backend: "openai_compatible",
    });
    expect(screen.getByText(/Stored key deleted/)).toBeTruthy();
  });

  it("hides Clear key for a local provider with no key env var", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "ollama" } });
    expect(screen.queryByRole("button", { name: /Clear key/ })).toBeNull();
  });

  it("renders the validate warning when the state carries one", () => {
    render(
      <ProviderSection
        state={{ ...state, providerWarning: "No strict JSON schema support; using json_object." }}
        busy={false}
        send={vi.fn()}
      />,
    );
    expect(screen.getByText(/No strict JSON schema support/)).toBeTruthy();
  });

  it("renders no warning when the state has none", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    expect(screen.queryByText(/⚠/)).toBeNull();
  });
});

describe("context window field", () => {
  it("prefills from the live backend value, not the table", () => {
    const withWindow: SettingsState = {
      ...state,
      provider: { backend: "gemini", model: "gemini-flash-latest", contextWindow: 262_144 },
    };
    render(<ProviderSection state={withWindow} busy={false} send={vi.fn()} />);
    expect((screen.getByLabelText(/Context window/) as HTMLInputElement).value).toBe("262144");
  });

  it("falls back to the starter table when the backend reports none", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    expect((screen.getByLabelText(/Context window/) as HTMLInputElement).value).toBe("1000000");
  });

  it("re-prefills from the table when the provider changes", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "anthropic" } });
    expect((screen.getByLabelText(/Context window/) as HTMLInputElement).value).toBe("200000");
  });

  it("sends the window with the save", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.change(screen.getByLabelText(/Context window/), { target: { value: "65536" } });
    fireEvent.click(screen.getByRole("button", { name: /Save & validate/ }));
    expect(send.mock.calls[0][0]).toMatchObject({ contextWindow: 65536 });
  });

  it("tells the user where to get the number and what each error costs", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    expect(screen.getByText(/model card/i)).toBeTruthy();
    expect(screen.getByText(/Too small/i)).toBeTruthy();
    expect(screen.getByText(/Too large/i)).toBeTruthy();
  });

  it("blocks the save and explains why when the window is below the backend floor", () => {
    /* Without this the value reaches the route, pydantic 422s, and the client
       discards the body — the user would see only "Backend request failed (422)". */
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.change(screen.getByLabelText(/Context window/), { target: { value: "500" } });
    expect(screen.getByText(/minimum is 1,024 tokens/i)).toBeTruthy();
    expect((screen.getByRole("button", { name: /Save & validate/ }) as HTMLButtonElement).disabled)
      .toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Save & validate/ }));
    expect(send).not.toHaveBeenCalled();
  });
});
