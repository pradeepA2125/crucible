import { render, screen, fireEvent, act } from "@testing-library/react";
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

function postResult(result: Record<string, unknown>) {
  // Wrapped in act() — matching the `deliver` helper in SettingsApp.test.tsx —
  // because React 18's automatic batching defers the state update from a raw
  // window.dispatchEvent to a microtask; asserting immediately after an
  // unwrapped dispatch is a real flake (observed here: it read as pass/fail
  // depending on unrelated timing, not on the assertion itself).
  act(() => {
    window.dispatchEvent(
      new MessageEvent("message", { data: { type: "settings/contextTestResult", result } }),
    );
  });
}

describe("context window Test button", () => {
  it("warns with the cost before running anything", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.click(screen.getByRole("button", { name: /^Test$/ }));
    expect(send).not.toHaveBeenCalled();  // first click only warns
    expect(screen.getByText(/1,000,000/)).toBeTruthy();  // the cost, spelled out
    expect(screen.getByText(/single request/i)).toBeTruthy();
  });

  it("runs only on the second, confirming click", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.click(screen.getByRole("button", { name: /^Test$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Run test/ }));
    expect(send).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "settings/testContextWindow", backend: "gemini", contextWindow: 1_000_000,
      }),
    );
  });

  it("can be cancelled without sending", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.click(screen.getByRole("button", { name: /^Test$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));
    expect(send).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Run test/ })).toBeNull();
  });

  it("reports recall as a pass, with the real token count", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: true, recalled: true, promptTokens: 998_123, exact: true });
    expect(screen.getByText(/998,123/)).toBeTruthy();
    expect(screen.getByText(/recalled/i)).toBeTruthy();
  });

  it("reports a successful call with no recall as a too-large window", () => {
    /* The silent-failure case: HTTP 200, tokens billed, nothing usable back. */
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: true, recalled: false, promptTokens: 998_123, exact: true });
    // Case-sensitive: the Task 9 help paragraph already contains "Too large"
    // (capitalized, sentence-initial); /too large/i collides with it and
    // throws "multiple elements found" — not a real ambiguity, since the
    // verdict text is lowercase mid-sentence ("...so this window is too
    // large."). The case-sensitive form targets the verdict specifically.
    expect(screen.getByText(/too large/)).toBeTruthy();
    // Pin against the green wording too — not just "amber text is present".
    // TestVerdict's branches are early-return and mutually exclusive today,
    // but that's an implementation detail a future refactor could break
    // silently; this is the single most important behaviour in the feature
    // (a silent provider failure must never read as success), so the test
    // should defend it directly rather than rely on someone re-reading the
    // component.
    expect(screen.queryByText(/passphrase recalled/i)).toBeNull();
  });

  it("labels an estimated token count as an estimate", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: true, recalled: true, promptTokens: 990_000, exact: false });
    expect(screen.getByText(/estimated/i)).toBeTruthy();
  });

  it("shows the provider's own error verbatim", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: false, recalled: false, error: "429 Too Many Requests" });
    expect(screen.getByText(/429 Too Many Requests/)).toBeTruthy();
  });

  it("clears a stale verdict when the window value changes after it renders", () => {
    /* A verdict describes a specific declared number. If it survives an edit
       to that number, it now describes a window the user has already moved
       away from — actively misleading, not merely stale. */
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: true, recalled: true, promptTokens: 998_123, exact: true });
    expect(screen.getByText(/recalled/i)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Context window/), { target: { value: "500000" } });
    expect(screen.queryByText(/recalled/i)).toBeNull();
  });

  it("clears a stale verdict when the provider changes after it renders", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: true, recalled: true, promptTokens: 998_123, exact: true });
    expect(screen.getByText(/recalled/i)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "anthropic" } });
    expect(screen.queryByText(/recalled/i)).toBeNull();
  });
});
