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
