import { render, screen, fireEvent, act } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { ModelMenu } from "./ModelMenu";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const LIST = {
  type: "modelList",
  current: { backend: "gemini", model: "gemini-flash-latest" },
  options: [
    { backend: "gemini", label: "Google Gemini", model: "gemini-flash-latest", active: true },
    { backend: "anthropic", label: "Anthropic", model: "claude-3-5-sonnet-latest", active: false },
  ],
};

function deliver(data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data }));
  });
}

describe("ModelMenu", () => {
  beforeEach(() => vi.clearAllMocks());

  it("requests the list on mount and shows the current model on the chip", () => {
    render(<ModelMenu />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "listModels" });
    deliver(LIST);
    expect(screen.getByRole("button", { name: /gemini-flash-latest/ })).toBeTruthy();
  });

  it("opens the popover and posts setModel for a non-active option", () => {
    render(<ModelMenu />);
    deliver(LIST);
    fireEvent.click(screen.getByRole("button", { name: /gemini-flash-latest/ }));
    fireEvent.click(screen.getByRole("button", { name: /Anthropic/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "setModel", backend: "anthropic", model: "claude-3-5-sonnet-latest",
    });
    deliver({ ...LIST, current: { backend: "anthropic", model: "claude-3-5-sonnet-latest" } });
    expect(screen.queryByText("Google Gemini")).toBeNull();
  });

  it("renders a swap error inside the open popover", () => {
    render(<ModelMenu />);
    deliver(LIST);
    fireEvent.click(screen.getByRole("button", { name: /gemini-flash-latest/ }));
    fireEvent.click(screen.getByRole("button", { name: /Anthropic/ }));
    deliver({ type: "modelSwapError", message: "validation failed: 401" });
    expect(screen.getByText(/validation failed: 401/)).toBeTruthy();
  });

  it("footer action posts openSettings", () => {
    render(<ModelMenu />);
    deliver(LIST);
    fireEvent.click(screen.getByRole("button", { name: /gemini-flash-latest/ }));
    fireEvent.click(screen.getByRole("button", { name: /Provider settings/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openSettings" });
  });
});

describe("ModelMenu list layout", () => {
  beforeEach(() => vi.clearAllMocks());
  it("names each provider once, one line per model, with effort outside the scrolling list", () => {
    render(<ModelMenu />);
    deliver({ type: "modelList", current: { backend: "claude_subscription", model: "b" }, options: [
      { backend: "claude_subscription", label: "Claude subscription", model: "a", display: "Claude Haiku", active: false },
      { backend: "claude_subscription", label: "Claude subscription", model: "b", display: "Claude Sonnet", active: true },
      { backend: "gemini", label: "Google Gemini", model: "g", active: false },
    ], effort: { level: "low", support: { supported: ["low", "high"], unsupported: {} } } });
    fireEvent.click(screen.getByRole("button", { name: /Model: Claude Sonnet/ }));
    const list = screen.getByTestId("model-list");
    expect(list.className).toContain("max-h-[240px]");
    expect(list.className).toContain("overflow-y-auto");
    expect(screen.getAllByText("Claude subscription")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Claude subscription: Claude Sonnet" })).toHaveAttribute("aria-pressed", "true");
    expect(list.contains(screen.getByRole("slider"))).toBe(false);
  });
});

describe("ModelMenu on the ChatGPT plan", () => {
  beforeEach(() => vi.clearAllMocks());

  const PLAN = {
    type: "modelList",
    usesChatgptPlan: true,
    current: { backend: "chatgpt", model: "gpt-a" },
    options: [
      { backend: "chatgpt", label: "ChatGPT plan", model: "gpt-a", display: "GPT A", active: true },
      { backend: "chatgpt", label: "ChatGPT plan", model: "gpt-b", display: "GPT B", active: false },
    ],
  };

  it("names plan models by display name and swaps between them", () => {
    render(<ModelMenu />);
    deliver(PLAN);
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT A/ }));
    fireEvent.click(screen.getByRole("button", { name: /GPT B/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith(
      { type: "setModel", backend: "chatgpt", model: "gpt-b" });
  });

  it("shows Using ChatGPT plan with a Manage usage link only while the plan is in use", () => {
    render(<ModelMenu />);
    deliver(PLAN);
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT A/ }));
    expect(screen.getByText("Using ChatGPT plan")).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: "Manage usage" }));
    expect(vscode.postMessage).toHaveBeenCalledWith(
      { type: "settings/openExternal", url: "https://chatgpt.com/settings/usage?tab=overview" });
    deliver(LIST);
    expect(screen.queryByText("Using ChatGPT plan")).toBeNull();
  });
});

describe("ModelMenu on the Claude plan", () => {
  const CLAUDE = {
    type: "modelList", usesClaudePlan: true,
    current: { backend: "claude_subscription", model: "claude-sonnet-5-5" },
    options: [
      { backend: "claude_subscription", label: "Claude subscription", model: "claude-sonnet-5-5", display: "Claude Sonnet 5.5", active: true },
    ],
  };

  it("shows the Claude indicator and opens Claude usage settings", () => {
    render(<ModelMenu />);
    deliver(CLAUDE);
    fireEvent.click(screen.getByRole("button", { name: /Model: Claude/ }));
    expect(screen.getByRole("button", { name: /Model: Claude Sonnet 5.5/ })).toBeTruthy();
    expect(screen.getByText("Using Claude plan")).toBeTruthy();
    expect(screen.queryByText("Using ChatGPT plan")).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "Manage usage" }));
    expect(vscode.postMessage).toHaveBeenLastCalledWith(
      { type: "settings/openExternal", url: "https://claude.ai/settings/usage" });
    deliver(LIST);
    expect(screen.queryByText("Using Claude plan")).toBeNull();
  });

  it("keeps Claude identification on a failed swap and clears it when the provider changes", () => {
    render(<ModelMenu />);
    deliver(CLAUDE);
    fireEvent.click(screen.getByRole("button", { name: /Model: Claude/ }));
    deliver({ type: "modelSwapError", message: "Connection expired" });
    expect(screen.getByText("Using Claude plan")).toBeTruthy();
    // Stale or contradictory flags must not identify an API-key provider as plan usage.
    deliver({ ...LIST, usesClaudePlan: true, usesChatgptPlan: true });
    expect(screen.queryByText("Using Claude plan")).toBeNull();
    expect(screen.queryByRole("link", { name: "Manage usage" })).toBeNull();
  });
});
