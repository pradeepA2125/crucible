import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ClaudePlanPanel } from "./ClaudePlanPanel";
import { ProviderAccessCard } from "../../components/ProviderAccessCard";
import { vscode } from "../vscodeApi";
import type { SettingsOutMsg, SettingsState } from "../types";
const REG = "claude_" + "a".repeat(24);
const state: SettingsState = { provider: { backend: "claude_subscription", model: "claude-sonnet-5-5" }, runtime: null, mcp: { enabled: false, servers: [] }, skills: [], envFlags: {}, restartRequired: false };
function deliver(data: SettingsOutMsg) { act(() => window.dispatchEvent(new MessageEvent("message", { data }))); }

describe("Claude subscription panel", () => {
  it("offers browser sign-in and a cancellable waiting state", () => {
    const send = vi.fn(); render(<ClaudePlanPanel state={state} busy={false} send={send} />);
    expect(screen.queryByText(/Using Claude subscription/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Continue with Claude" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/claudeSignIn" });
    deliver({ type: "settings/claudeSignIn", signIn: { attemptId: "a", state: "pending", registrationId: null, reason: null, message: null } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(send).toHaveBeenLastCalledWith({ type: "settings/claudeCancel" });
  });
  it("uses a connection, manages plan and usage, and disconnects without cancelling the subscription", () => {
    const send = vi.fn(); render(<ClaudePlanPanel state={state} busy={false} send={send} />);
    deliver({ type: "settings/claude", activeRegistrationId: REG, accounts: [{ registrationId: REG, label: "Claude", signedIn: true }] });
    expect(screen.getByRole("button", { name: "Save & validate Claude" })).toHaveProperty("disabled", true);
    deliver({ type: "settings/claudeModels", registrationId: REG, models: [{ slug: "claude-sonnet-5-5", displayName: "Claude Sonnet 5.5", contextWindow: 1000000, maxTokens: 128000 }] });
    expect(screen.getByText(/1,000,000 tokens/)).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText(/Using Claude subscription/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save & validate Claude" }));
    expect(send).toHaveBeenLastCalledWith({ type: "settings/useClaude", registrationId: REG, model: "claude-sonnet-5-5" });
    fireEvent.click(screen.getByRole("button", { name: "Manage plan" }));
    expect(vscode.postMessage).toHaveBeenLastCalledWith({ type: "settings/openExternal", url: "https://claude.ai/settings/billing" });
    fireEvent.click(screen.getByRole("button", { name: "Manage usage" }));
    expect(vscode.postMessage).toHaveBeenLastCalledWith({ type: "settings/openExternal", url: "https://claude.ai/settings/usage" });
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(send).toHaveBeenLastCalledWith({ type: "settings/claudeSignOut", registrationId: REG });
    deliver({ type: "settings/claudeSignedOut", registrationId: REG });
    expect(screen.getByText(/subscription is unchanged/)).toBeTruthy();
  });
  it("routes a Claude usage stop to Claude settings", () => {
    render(<ProviderAccessCard access={{ provider: "claude_subscription", kind: "usage_limit", message: "Claude limit", code: null, status: 429, requestId: null }} />);
    expect(screen.queryByText("ChatGPT")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Manage usage" }));
    expect(vscode.postMessage).toHaveBeenLastCalledWith({ type: "settings/openExternal", url: "https://claude.ai/settings/usage" });
  });
});

it("ignores catalog responses for a previous account and allows retry after failure", () => {
  const send = vi.fn(); render(<ClaudePlanPanel state={state} busy={false} send={send} />);
  const other = "claude_" + "b".repeat(24);
  deliver({ type: "settings/claude", activeRegistrationId: REG, accounts: [
    { registrationId: REG, label: "First", signedIn: true },
    { registrationId: other, label: "Second", signedIn: true },
  ] });
  fireEvent.change(screen.getByRole("combobox", { name: "Claude connection" }), { target: { value: other } });
  deliver({ type: "settings/claudeModels", registrationId: REG, models: [
    { slug: "claude-stale", displayName: "Stale model", contextWindow: 1000000, maxTokens: 128000 },
  ] });
  expect(screen.queryByRole("option", { name: "Stale model" })).toBeNull();
  expect(screen.getByRole("button", { name: "Use this connection" })).toHaveProperty("disabled", true);
  deliver({ type: "settings/claudeError", registrationId: other, message: "Catalog unavailable" });
  expect(screen.getByText("Catalog unavailable")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Refresh models" }));
  expect(send).toHaveBeenLastCalledWith({ type: "settings/claudeModels", registrationId: other, refresh: true });
  deliver({ type: "settings/claudeModels", registrationId: other, models: [
    { slug: "claude-unknown", displayName: "Unknown window", contextWindow: null, maxTokens: null },
    { slug: "claude-small", displayName: "Small model", contextWindow: 200000, maxTokens: 64000 },
  ] });
  expect(screen.getByRole("option", { name: "Unknown window" })).toHaveProperty("disabled", true);
  expect(screen.getByText(/200,000 tokens/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Use this connection" }));
  expect(send).toHaveBeenLastCalledWith({ type: "settings/useClaude", registrationId: other, model: "claude-small" });
});
