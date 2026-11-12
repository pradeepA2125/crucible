import { render, screen, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
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

describe("SetupApp — local voice", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => { delete (window as { crucibleVoice?: unknown }).crucibleVoice; });

  it("downloads speech recognition and then spoken replies, with Cancel covering both", async () => {
    const installs: { model: string; resolve(): void; reject(): void }[] = [];
    const request = vi.fn(async (command: { type: string; model?: string }) => {
      if (command.type === "status") return { available: true, pocketAvailable: true };
      if (command.type === "install") return new Promise<void>((resolve, reject) => installs.push({ model: command.model!, resolve, reject }));
      return {};
    });
    (window as { crucibleVoice?: unknown }).crucibleVoice = { request, subscribe: () => () => {} };
    render(<SetupApp />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(await screen.findByLabelText("Enable local voice"));
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    expect(screen.getByText("Downloading speech recognition (Whisper Small, 1 of 2)…")).toBeTruthy();
    expect(installs.map(install => install.model)).toEqual(["whisper-small"]);
    await act(async () => { installs[0].resolve(); });
    expect(await screen.findByText("Downloading spoken replies (Pocket TTS, 2 of 2)…")).toBeTruthy();
    expect(installs.map(install => install.model)).toEqual(["whisper-small", "pocket"]);
    fireEvent.click(screen.getByRole("button", { name: "Cancel voice download" }));
    expect(request).toHaveBeenCalledWith({ type: "cancelInstall" });
    expect(screen.getByText(/Voice download cancelled/)).toBeTruthy();
    await act(async () => { installs[1].reject(); });
    expect(screen.getByText(/Voice download cancelled/)).toBeTruthy();
  });

  it("finishes after speech recognition alone where spoken replies cannot run", async () => {
    const request = vi.fn(async (command: { type: string }) =>
      command.type === "status" ? { available: true, pocketAvailable: false } : {});
    (window as { crucibleVoice?: unknown }).crucibleVoice = { request, subscribe: () => () => {} };
    render(<SetupApp />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(await screen.findByLabelText("Enable local voice"));
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    expect(await screen.findByText("Local voice is ready.")).toBeTruthy();
    expect(request.mock.calls.filter(([command]) => command.type === "install")).toHaveLength(1);
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

it("an install the host refuses shows its reason and a Retry instead of sitting on pending rows", () => {
  render(<SetupApp />);
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
  deliver({ type: "setup/error", message: "pradeep-site is still working. Let it finish or stop it, then retry the install." });
  expect(screen.getByRole("alert").textContent).toMatch(/still working/);
  vi.mocked(vscode.postMessage).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(vscode.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "setup/install" }));
  expect(screen.queryByRole("alert")).toBeNull();
});

it("after an app update Setup says the runtime must be updated, and keeps projects and settings", () => {
  render(<SetupApp />);
  deliver({ type: "setup/preferences", localModels: true, runtimeUpdate: { installed: "v0.9.1", bundled: "v0.9.4" } });
  const notice = screen.getByRole("status");
  expect(notice.textContent).toMatch(/updated/);
  expect(notice.textContent).toMatch(/v0\.9\.1/);
  expect(notice.textContent).toMatch(/v0\.9\.4/);
  expect(notice.textContent).toMatch(/projects.*settings.*kept/i);
});

it("after an update install it resumes with the saved provider instead of asking for one again", () => {
  render(<SetupApp />);
  deliver({ type: "setup/preferences", localModels: true, runtimeUpdate: { installed: "v0.9.1", bundled: "v0.9.4" } });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
  vi.mocked(vscode.postMessage).mockClear();
  deliver({ type: "setup/installDone", ok: true });
  expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/resume" });
  deliver({ type: "setup/ready", port: 8123, provider: { backend: "ollama", model: "glm-4.7-flash:latest" } });
  expect(screen.getByRole("button", { name: "Open chat" })).toBeTruthy();
  // The provider that actually started, not the form's untouched default.
  expect(screen.getByText("ollama")).toBeTruthy();
  expect(screen.getByText("glm-4.7-flash:latest")).toBeTruthy();
});

