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
    fireEvent.click(screen.getByRole("menuitem", { name: /Anthropic/ }));
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
    fireEvent.click(screen.getByRole("menuitem", { name: /Anthropic/ }));
    deliver({ type: "modelSwapError", message: "validation failed: 401" });
    expect(screen.getByText(/validation failed: 401/)).toBeTruthy();
  });

  it("footer action posts openSettings", () => {
    render(<ModelMenu />);
    deliver(LIST);
    fireEvent.click(screen.getByRole("button", { name: /gemini-flash-latest/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Provider settings/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openSettings" });
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
    fireEvent.click(screen.getByRole("button", { name: "Model: GPT A" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /GPT B/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith(
      { type: "setModel", backend: "chatgpt", model: "gpt-b" });
  });

  it("shows Using ChatGPT plan with a Manage usage link only while the plan is in use", () => {
    render(<ModelMenu />);
    deliver(PLAN);
    expect(screen.getByText("Using ChatGPT plan")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Manage usage" }));
    expect(vscode.postMessage).toHaveBeenCalledWith(
      { type: "settings/openExternal", url: "https://chatgpt.com/settings/usage?tab=overview" });
    deliver(LIST);
    expect(screen.queryByText("Using ChatGPT plan")).toBeNull();
  });
});
