import { render, screen, fireEvent, act } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import SetupApp from "./SetupApp";
import { vscode } from "./vscodeApi";

vi.mock("./vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

function deliver(data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data }));
  });
}

describe("SetupApp", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the host-owned runtime destination while retaining the VS Code default", () => {
    render(<SetupApp />);
    expect(screen.getByText("~/.crucible/runtime")).toBeTruthy();
    deliver({ type: "setup/preferences", localModels: true, runtimePath: "/disposable/desktop-profile/runtime" });
    expect(screen.getByText("/disposable/desktop-profile/runtime")).toBeTruthy();
    expect(screen.queryByText("~/.crucible/runtime")).toBeNull();
  });

  it("restores the saved opt-out and ignores late preferences after a new choice", () => {
    render(<SetupApp />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/preferences" });
    deliver({ type: "setup/preferences", localModels: false });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    fireEvent.click(checkbox);
    deliver({ type: "setup/preferences", localModels: false });
    expect(checkbox.checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/install", localModels: true });
  });

  it("lets the user opt out before any package installation starts", () => {
    render(<SetupApp />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(vscode.postMessage).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "setup/install" }));
    const choice = screen.getByRole("checkbox");
    expect((choice as HTMLInputElement).checked).toBe(true);
    fireEvent.click(choice);
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "setup/install", localModels: false,
    });
  });
  it("walks welcome → models → install → provider → done", () => {
    render(<SetupApp />);
    expect(screen.getByLabelText(/Step 1 of 5/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/install", localModels: true });
    deliver({ type: "setup/progress", component: "uv", status: "done" });
    deliver({ type: "setup/installDone", ok: true });
    expect(screen.getByLabelText("Model")).toBeTruthy();
    deliver({ type: "setup/ready", port: 8090 });
    expect(screen.getByText(/8090/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Open chat/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/openChat" });
  });

  it("shows Retry when install fails", () => {
    render(<SetupApp />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    deliver({ type: "setup/progress", component: "agentd", status: "failed", detail: "pip exploded" });
    deliver({ type: "setup/installDone", ok: false });
    expect(screen.getByText("pip exploded")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
  });
});

describe("SetupApp — Use your ChatGPT plan", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the host-owned runtime destination while retaining the VS Code default", () => {
    render(<SetupApp />);
    expect(screen.getByText("~/.crucible/runtime")).toBeTruthy();
    deliver({ type: "setup/preferences", localModels: true, runtimePath: "/disposable/desktop-profile/runtime" });
    expect(screen.getByText("/disposable/desktop-profile/runtime")).toBeTruthy();
    expect(screen.queryByText("~/.crucible/runtime")).toBeNull();
  });

  function toProviderStep() {
    render(<SetupApp />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    deliver({ type: "setup/installDone", ok: true });
  }

  it("offers the plan during onboarding, alongside (not instead of) the API-key path", () => {
    toProviderStep();
    expect(screen.getByText("Use your ChatGPT plan")).toBeTruthy();
    expect(screen.getByText("Or use your own API key")).toBeTruthy();
    // ChatGPT is not an API-key provider: it isn't in the key form's dropdown.
    const options = Array.from(screen.getByLabelText("Provider").querySelectorAll("option"))
      .map((o) => o.textContent);
    expect(options).not.toContain("ChatGPT plan");
  });

  it("starts the backend, signs in when no account is saved, and finishes on use", () => {
    toProviderStep();
    fireEvent.click(screen.getByRole("button", { name: "Continue with ChatGPT" }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/startForChatGPT" });
    expect(screen.getByRole("status").textContent).toMatch(/Starting Crucible/);
    deliver({ type: "setup/chatgptReady", hasAccounts: false });
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/chatgptSignIn" });
    deliver({ type: "setup/ready", port: 8095 });
    expect(screen.getByText(/using your ChatGPT plan/)).toBeTruthy();
  });

  it("a returning user picks a saved account instead of registering again", () => {
    toProviderStep();
    fireEvent.click(screen.getByRole("button", { name: "Continue with ChatGPT" }));
    deliver({ type: "setup/chatgptReady", hasAccounts: true });
    expect(vscode.postMessage).not.toHaveBeenCalledWith({ type: "settings/chatgptSignIn" });
  });

  it("a failed start returns to the offer with the error", () => {
    toProviderStep();
    fireEvent.click(screen.getByRole("button", { name: "Continue with ChatGPT" }));
    deliver({ type: "setup/error", message: "backend did not start" });
    expect(screen.getByText("backend did not start")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue with ChatGPT" })).toBeTruthy();
  });
});

it("starts Claude setup and waits for the connection to be put into use", () => {
  render(<SetupApp />);
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
  deliver({ type: "setup/installDone", ok: true });
  fireEvent.click(screen.getByRole("button", { name: "Continue with Claude" }));
  expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/startForClaude" });
  deliver({ type: "setup/claudeReady" });
  expect(screen.getByRole("button", { name: "Continue with Claude" })).toBeTruthy();
  deliver({ type: "settings/claudeSignIn", signIn: { attemptId: "a", state: "succeeded", registrationId: "claude_test", reason: null, message: null } });
  expect(screen.queryByText(/using your Claude subscription/)).toBeNull();
  deliver({ type: "setup/ready", port: 8090 });
  expect(screen.getByText(/using your Claude subscription/)).toBeTruthy();
});
