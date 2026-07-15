import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ThreadView } from "./ThreadView";
import { vscode } from "../vscodeApi";
import type { AppState } from "../types";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

// The overlay body is the full settings app (own tests). Stub it so ThreadView
// integration only asserts the overlay opens at the right section.
vi.mock("../settings/SettingsApp", () => ({
  default: ({ initialSection }: { initialSection?: string }) => (
    <div data-testid="settings-app">section:{initialSection ?? "overview"}</div>
  ),
}));

const base: AppState = {
  view: "thread",
  threads: [],
  activeThreadId: "",
  messages: [],
  streaming: null,
  thinkingStatus: null,
  inputEnabled: true,
  liveGate: null,
  livePlan: null,
  liveReview: null,
  liveError: null,
  liveTodos: null,
  liveSessions: null,
  sessionTranscripts: {},
  workbar: null,
  retryStatus: null,
  liveStatus: null,
  turnActive: false,
};

function renderView() {
  return render(
    <ThreadView state={base} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />,
  );
}

describe("ThreadView settings overlay", () => {
  it("opens the settings popup at the picked section and closes the drawer", () => {
    renderView();
    // Overlay hidden initially.
    expect(screen.queryByTestId("settings-app")).toBeNull();

    // Open the drawer (☰) and pick Provider.
    fireEvent.click(screen.getByRole("button", { name: /settings menu/i }));
    const drawerNav = screen.getByRole("navigation", { name: "Settings sections" });
    fireEvent.click(within(drawerNav).getByRole("button", { name: /provider/i }));

    // Popup opens at provider; the drawer nav is gone (closed).
    expect(screen.getByTestId("settings-app").textContent).toContain("provider");
    expect(screen.queryByRole("navigation", { name: "Settings sections" })).toBeNull();
  });

  it("opens the settings popup at Overview from the composer gear", () => {
    renderView();
    expect(screen.queryByTestId("settings-app")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /open settings/i }));
    expect(screen.getByTestId("settings-app").textContent).toContain("overview");
  });

  it("closes the popup on ✕", () => {
    renderView();
    fireEvent.click(screen.getByRole("button", { name: /open settings/i }));
    expect(screen.getByTestId("settings-app")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /close settings/i }));
    expect(screen.queryByTestId("settings-app")).toBeNull();
  });
});

describe("ThreadView memory shortcut", () => {
  it("posts openMemoryPanel when the memory inspector button is clicked", () => {
    renderView();
    fireEvent.click(screen.getByRole("button", { name: /memory inspector/i }));
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({ type: "openMemoryPanel" });
  });
});

describe("ThreadView retry-status bubble", () => {
  it("renders the retry bubble with the retry message when retryStatus is set", () => {
    const state: AppState = {
      ...base,
      retryStatus: { attempt: 2, max_attempts: 4, reason: "rate_limited", message: "⏳ Rate limited — retrying in 8s (attempt 2/4)…" },
    };

    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);

    expect(screen.getByText(/Rate limited — retrying in 8s \(attempt 2\/4\)/)).toBeInTheDocument();
  });

  it("retry bubble takes precedence over thinkingStatus and the streaming bubble", () => {
    const state: AppState = {
      ...base,
      retryStatus: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      thinkingStatus: "Thinking…",
      streaming: { text: "partial answer", thinkingEntries: [], activeThinkingChunk: "", toolEvents: [] },
    };

    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);

    expect(screen.getByText(/retrying/)).toBeInTheDocument();
    expect(screen.queryByText("partial answer")).not.toBeInTheDocument();
  });

  it("renders normally (no retry bubble) once retryStatus clears", () => {
    const state: AppState = {
      ...base,
      retryStatus: null,
      streaming: { text: "partial answer", thinkingEntries: [], activeThinkingChunk: "", toolEvents: [] },
    };

    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);

    expect(screen.getByText("partial answer")).toBeInTheDocument();
  });
});
