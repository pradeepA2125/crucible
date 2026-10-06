import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatGPTPlanPanel } from "./ChatGPTPlanPanel";
import { vscode } from "../vscodeApi";
import type { ChatGPTAccount, SettingsOutMsg, SettingsState } from "../types";

const REG = "reg_aaaaaaaaaaaa";
const OTHER = "reg_bbbbbbbbbbbb";

function stateFor(backend: string, model = "m"): SettingsState {
  return { provider: { backend, model }, runtime: null, mcp: { enabled: false, servers: [] },
           skills: [], envFlags: {}, restartRequired: false };
}

function account(overrides: Partial<ChatGPTAccount> = {}): ChatGPTAccount {
  return { registrationId: REG, label: "a@x.com", email: "a@x.com", name: null,
           planEnabled: true, signedIn: true, ...overrides };
}

function deliver(data: SettingsOutMsg) {
  act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
}

describe("ChatGPTPlanPanel", () => {
  it("offers the plan with the approved Continue with ChatGPT button", () => {
    const send = vi.fn();
    render(<ChatGPTPlanPanel state={stateFor("gemini")} busy={false} send={send} />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/chatgptLoad" });
    expect(screen.getByText("Use your ChatGPT plan")).toBeTruthy();
    expect(screen.getByText(/usage included in your ChatGPT plan or credits balance/)).toBeTruthy();
    const button = screen.getByRole("button", { name: "Continue with ChatGPT" });
    expect(button.getAttribute("data-format")).toBe("continue-white");
    fireEvent.click(button);
    expect(send).toHaveBeenCalledWith({ type: "settings/chatgptSignIn" });
  });

  it("shows the browser step and cancels it", () => {
    const send = vi.fn();
    render(<ChatGPTPlanPanel state={stateFor("gemini")} busy={false} send={send} />);
    deliver({ type: "settings/chatgptSignIn", signIn: {
      attemptId: "att_1", state: "pending", registrationId: null, planEnabled: null,
      firstPlanSignIn: false, reason: null, message: null } });
    expect(screen.getByRole("status").textContent).toMatch(/in your browser/);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/chatgptCancel" });
  });

  it("lists accounts with the action each state needs", () => {
    const send = vi.fn();
    render(<ChatGPTPlanPanel state={stateFor("gemini")} busy={false} send={send} />);
    deliver({ type: "settings/chatgpt", activeRegistrationId: null, accounts: [
      account(),
      account({ registrationId: OTHER, label: "a@x.com (2)", planEnabled: false }),
      account({ registrationId: "reg_cccccccccccc", label: "b@x.com", signedIn: false }),
    ] });
    expect(screen.getByText("Plan usage on")).toBeTruthy();
    expect(screen.getByText("Plan usage off")).toBeTruthy();
    expect(screen.getByText("Signed out")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Enable ChatGPT plan usage" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/chatgptSignIn", registrationId: OTHER,
                                        reconsent: true });
    fireEvent.click(screen.getByRole("button", { name: "Sign in again" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/chatgptSignIn",
                                        registrationId: "reg_cccccccccccc" });
  });

  it("picks a model from the account's own catalog and puts the account to use", () => {
    const send = vi.fn();
    render(<ChatGPTPlanPanel state={stateFor("gemini")} busy={false} send={send} />);
    deliver({ type: "settings/chatgpt", activeRegistrationId: null, accounts: [account()] });
    expect(vscode.postMessage).toHaveBeenCalledWith(
      { type: "settings/chatgptModels", registrationId: REG });
    deliver({ type: "settings/chatgptModels", registrationId: REG, models: [
      { slug: "gpt-a", displayName: "GPT A" }, { slug: "gpt-b", displayName: "GPT B" }] });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gpt-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Use this account" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/useChatGPT", registrationId: REG,
                                        model: "gpt-b" });
  });

  it("while in use, shows Using ChatGPT plan with a Manage usage link", () => {
    const send = vi.fn();
    render(<ChatGPTPlanPanel state={stateFor("chatgpt", "gpt-a")} busy={false} send={send} />);
    deliver({ type: "settings/chatgpt", activeRegistrationId: REG, accounts: [account()] });
    expect(screen.getByText("In use")).toBeTruthy();
    expect(screen.getByText("Using ChatGPT plan")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Manage usage" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/openExternal",
                                        url: "https://chatgpt.com/#settings" });
  });

  it("says when sign-out could not be confirmed remotely", () => {
    render(<ChatGPTPlanPanel state={stateFor("gemini")} busy={false} send={vi.fn()} />);
    deliver({ type: "settings/chatgptSignedOut", registrationId: REG, remoteRevoked: false });
    expect(screen.getByText(/didn't confirm the session ended/)).toBeTruthy();
  });

  it("welcomes a first plan sign-in once and records the dismissal", () => {
    const send = vi.fn();
    render(<ChatGPTPlanPanel state={stateFor("gemini")} busy={false} send={send} />);
    deliver({ type: "settings/chatgptWelcome", registrationId: REG });
    expect(screen.getByRole("dialog", { name: "You're using your ChatGPT plan" })).toBeTruthy();
    expect(screen.getByText(/Eligible usage in this app uses your ChatGPT plan/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/chatgptWelcomed", registrationId: REG });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
