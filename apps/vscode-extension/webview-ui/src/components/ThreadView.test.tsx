import { render, screen, fireEvent, within, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ThreadView } from "./ThreadView";
import { vscode } from "../vscodeApi";
import type { AppState } from "../types";
import { contextUsage } from "../test/contextUsageFixture";

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
  liveGates: [],
  livePlan: null,
  liveReview: null,
  liveError: null,
  liveTodos: null,
  providerAccess: null, threadUsage: null,
  contextUsage: null, contextUsageStale: false,
  liveSessions: null,
  sessionTranscripts: {},
  workbar: null,
  retryStatus: null,
  tokenProgress: null,
  editFailure: null,
  liveStatus: null,
  turnActive: false,
  turnKind: null,
  queuedIds: [],
  agentsRunning: 0,
  planMode: false,
  stepReview: true,
  agents: {},
  agentViews: {},
  teams: {},
  teamViews: {},
};

function renderView() {
  return render(
    <ThreadView state={base} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />,
  );
}

describe("ThreadView context integration", () => {
  it("forwards the authoritative snapshot and stale flag without replacing Manage usage", () => {
    render(<ThreadView state={{ ...base, contextUsage, contextUsageStale: true }}
      onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    expect(screen.getByRole("button", { name: /context.*details/i })).toHaveTextContent("Context ~18% · stale");
    act(() => window.dispatchEvent(new MessageEvent("message", { data: {
      type: "modelList", usesChatgptPlan: true,
      current: { backend: "chatgpt", model: "different-composer-model" }, options: [],
    } })));
    expect(screen.getByRole("button", { name: /context.*details/i })).toHaveTextContent("Context ~18% · stale");
    fireEvent.click(screen.getByRole("button", { name: /manage usage/i }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/openExternal", url: "https://chatgpt.com/settings/usage?tab=overview" });
  });
});

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

describe("ThreadView — transient activity line", () => {
  const bubble = { text: "", thinkingEntries: [], activeThinkingChunk: "", toolEvents: [
    { id: 1, tool: "run_command", args: { command: "pytest" }, source: "explore" as const, done: false },
  ] };

  it("shows the running tool while a turn is active and a bubble exists", () => {
    render(<ThreadView state={{ ...base, turnActive: true, inputEnabled: false, streaming: bubble }}
      onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    expect(screen.getByTestId("transient-activity").textContent).toContain("Running `pytest`");
  });

  it("hides it while a retry is in flight", () => {
    render(<ThreadView state={{ ...base, turnActive: true, inputEnabled: false, streaming: bubble,
      retryStatus: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" } }}
      onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    expect(screen.queryByTestId("transient-activity")).toBeNull();
  });
});

describe("ThreadView — Stop all agents (spec §6)", () => {
  it("shows Stop all agents only while agents run", () => {
    const { rerender } = render(
      <ThreadView state={base} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />,
    );
    expect(screen.queryByTitle("Stop all agents")).toBeNull();
    rerender(
      <ThreadView state={{ ...base, agentsRunning: 2 }} onBack={() => {}} dismissedErrorTaskId={null}
                  onDismissError={() => {}} />,
    );
    fireEvent.click(screen.getByTitle("Stop all agents"));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "stopAllAgents" });
  });
});
