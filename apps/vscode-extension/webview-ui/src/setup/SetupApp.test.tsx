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

  it("walks welcome → install → provider → done", () => {
    render(<SetupApp />);
    expect(screen.getByLabelText(/Step 1 of 4/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/install" });
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
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    deliver({ type: "setup/progress", component: "agentd", status: "failed", detail: "pip exploded" });
    deliver({ type: "setup/installDone", ok: false });
    expect(screen.getByText("pip exploded")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
  });
});

describe("SetupApp — Use your ChatGPT plan", () => {
  beforeEach(() => vi.clearAllMocks());

  function toProviderStep() {
    render(<SetupApp />);
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
