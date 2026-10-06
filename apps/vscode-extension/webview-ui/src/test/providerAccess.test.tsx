import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExtensionMessage, ProviderAccessView } from "../types";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { vscode } from "../vscodeApi";
import { useAppState } from "../hooks/useAppState";
import { ProviderAccessCard } from "../components/ProviderAccessCard";

function access(kind: string, message = "m"): ProviderAccessView {
  return { kind, message, code: null, status: null, requestId: null };
}

describe("provider access card", () => {
  beforeEach(() => vi.clearAllMocks());

  it("is set and cleared from host messages", () => {
    const { result } = renderHook(() => useAppState());
    const post = (data: ExtensionMessage) =>
      act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
    post({ type: "renderProviderAccess", access: access("usage_limit") });
    expect(result.current.state.providerAccess?.kind).toBe("usage_limit");
    post({ type: "renderProviderAccess", access: null });
    expect(result.current.state.providerAccess).toBeNull();
  });

  it("usage limit: Manage usage is the primary action, ChatGPT identity visible", () => {
    render(<ProviderAccessCard access={access("usage_limit")} />);
    expect(screen.getByText("Usage limit reached")).toBeTruthy();
    expect(screen.getByText("Review your plan or this app's limit in ChatGPT settings.")).toBeTruthy();
    expect(screen.getByText("ChatGPT")).toBeTruthy();
    expect(screen.queryByText(/Buy/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Manage usage" }));
    expect(vscode.postMessage).toHaveBeenCalledWith(
      { type: "settings/openExternal", url: "https://chatgpt.com/settings/usage?tab=overview" });
  });

  it.each([
    ["session_invalid", "Sign in again"],
    ["plan_disabled", "Enable in settings"],
    ["not_eligible", "Use an API key instead"],
  ])("%s sends the user to provider settings", (kind, label) => {
    render(<ProviderAccessCard access={access(kind, "why")} />);
    expect(screen.getByText("why")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openSettings", section: "provider" });
  });

  it("an unknown kind still renders, with its request id", () => {
    render(<ProviderAccessCard access={{ ...access("misconfigured", "bad grant"), requestId: "req_7" }} />);
    expect(screen.getByText("bad grant (request req_7)")).toBeTruthy();
  });
});
