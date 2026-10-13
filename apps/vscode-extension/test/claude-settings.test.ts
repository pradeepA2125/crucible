import { describe, expect, it, vi } from "vitest";
import { createClaudeHandler, type ClaudeDeps, type ClaudeOutMsg } from "../src/claude-settings.js";
import type { ClaudeSignIn } from "@crucible/editor-client";

const status: ClaudeSignIn = { attemptId: "attempt", state: "pending", registrationId: null, reason: null, message: null };
const REG = "claude_" + "a".repeat(24);
function setup() {
  const post = vi.fn<(msg: ClaudeOutMsg) => void>();
  const mutation = vi.fn(async (_kind: "connection" | "provider") => {});
  const deps: ClaudeDeps = {
    client: {
      startClaudeSignIn: vi.fn(async () => ({ ...status, authorizeUrl: "https://claude.ai/oauth/authorize?state=abc" })),
      getClaudeSignIn: vi.fn(async () => ({ ...status, state: "succeeded" as const, registrationId: REG })),
      cancelClaudeSignIn: vi.fn(async () => ({ ...status, state: "failed" as const, reason: "cancelled" })),
      listClaudeAccounts: vi.fn(async () => [{ registrationId: REG, label: "Claude", signedIn: true }]),
      listClaudeModels: vi.fn(async () => [{ slug: "claude-sonnet-5-5", displayName: "Claude Sonnet 5.5", contextWindow: 1000000, maxTokens: 128000 }]),
      signOutClaude: vi.fn(async () => ({ remoteRevoked: false })),
      setProvider: vi.fn(async req => ({ backend: req.backend, model: req.model ?? "", contextWindow: 1000000 })),
    },
    openExternal: vi.fn(async () => {}), activeRegistration: () => REG,
    saveActiveRegistration: vi.fn(async () => {}), saveContextWindow: vi.fn(async () => {}),
    sleep: vi.fn(async () => {}), now: () => 0,
  };
  return { deps, post, mutation, handle: createClaudeHandler(deps, post, mutation) };
}

describe("Claude connection host", () => {
  it("opens a validated browser destination and never forwards the URL to the webview", async () => {
    const s = setup(); await s.handle({ type: "settings/claudeSignIn" });
    expect(s.deps.openExternal).toHaveBeenCalledWith("https://claude.ai/oauth/authorize?state=abc");
    expect(JSON.stringify(s.post.mock.calls)).not.toContain("authorizeUrl");
    expect(s.mutation).toHaveBeenCalledWith("connection");
  });
  it("cancels a forged login URL", async () => {
    const s = setup(); s.deps.client.startClaudeSignIn = async () => ({ ...status, authorizeUrl: "https://evil.test/oauth/authorize" });
    await s.handle({ type: "settings/claudeSignIn" });
    expect(s.deps.openExternal).not.toHaveBeenCalled();
    expect(s.deps.client.cancelClaudeSignIn).toHaveBeenCalledWith("attempt");
    expect(s.post).toHaveBeenLastCalledWith({ type: "settings/claudeError", message: "Invalid Claude sign-in destination." });
  });
  it("saves the connection id only after the provider successfully validates", async () => {
    const s = setup();
    await s.handle({ type: "settings/useClaude", registrationId: REG, model: "claude-sonnet-5-5" });
    expect(s.deps.client.setProvider).toHaveBeenCalledWith({ backend: "claude_subscription", model: "claude-sonnet-5-5", credentials: { CRUCIBLE_CLAUDE_REGISTRATION: REG } });
    expect(s.deps.saveActiveRegistration).toHaveBeenCalledWith(REG);
    expect(s.deps.saveContextWindow).toHaveBeenCalledWith(1000000);
    expect(s.mutation).toHaveBeenCalledWith("provider");
    s.deps.client.setProvider = async () => { throw new Error("Connection expired"); };
    vi.mocked(s.deps.saveActiveRegistration).mockClear();
    await s.handle({ type: "settings/useClaude", registrationId: REG, model: "m" });
    expect(s.deps.saveActiveRegistration).not.toHaveBeenCalled();
  });
  it("does not publish stale success when cancel races polling", async () => {
    const s = setup();
    let release!: () => void;
    s.deps.sleep = () => new Promise<void>(resolve => { release = resolve; });
    const login = s.handle({ type: "settings/claudeSignIn" });
    await vi.waitFor(() => expect(release).toBeDefined());
    await s.handle({ type: "settings/claudeCancel" }); release(); await login;
    expect(s.deps.client.getClaudeSignIn).not.toHaveBeenCalled();
    expect(s.mutation).not.toHaveBeenCalled();
  });
  it("disconnects locally and refreshes public account status", async () => {
    const s = setup(); await s.handle({ type: "settings/claudeSignOut", registrationId: REG });
    expect(s.deps.client.signOutClaude).toHaveBeenCalledWith(REG);
    expect(s.post).toHaveBeenCalledWith({ type: "settings/claudeSignedOut", registrationId: REG });
  });
});

it("loads catalog metadata and scopes catalog failures to the requested account", async () => {
  const s = setup();
  await s.handle({ type: "settings/claudeModels", registrationId: REG });
  expect(s.post).toHaveBeenLastCalledWith({ type: "settings/claudeModels", registrationId: REG, models: [
    { slug: "claude-sonnet-5-5", displayName: "Claude Sonnet 5.5", contextWindow: 1000000, maxTokens: 128000 },
  ] });
  s.deps.client.listClaudeModels = async () => { throw new Error("Catalog unavailable"); };
  await s.handle({ type: "settings/claudeModels", registrationId: REG });
  expect(s.post).toHaveBeenLastCalledWith({ type: "settings/claudeError", registrationId: REG, message: "Catalog unavailable" });
});
