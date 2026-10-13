import { expect, it } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";

it("maps Claude auth routes with encoded identifiers and secret-free public types", async () => {
  const sent: { url: string; method: string }[] = [];
  const client = new HttpBackendClient({ baseUrl: "http://localhost:8000", fetchFn: async (url, init) => {
    sent.push({ url: String(url), method: init?.method ?? "GET" });
    return new Response(JSON.stringify(String(url).endsWith("/registrations") ? { registrations: [{ registration_id: "id", label: "Claude", signed_in: true, access_token: "must-drop" }] }
      : String(url).endsWith("/models") ? { models: [{ slug: "claude-sonnet-5-5", display_name: "Claude Sonnet 5.5", context_window: 1000000, max_tokens: 128000, access_token: "must-drop" }] }
      : String(url).endsWith("/sign-out") ? { remote_revoked: false }
      : { attempt_id: "a", state: "pending", registration_id: null, reason: null, message: null, authorize_url: "https://claude.ai/oauth/authorize" }), { headers: { "content-type": "application/json" } });
  } });
  expect(await client.startClaudeSignIn()).toMatchObject({ attemptId: "a", authorizeUrl: "https://claude.ai/oauth/authorize" });
  expect(await client.listClaudeAccounts()).toEqual([{ registrationId: "id", label: "Claude", signedIn: true }]);
  expect(await client.listClaudeModels("id/a")).toEqual([{ slug: "claude-sonnet-5-5", displayName: "Claude Sonnet 5.5", contextWindow: 1000000, maxTokens: 128000 }]);
  await client.getClaudeSignIn("a/b"); await client.cancelClaudeSignIn("a/b");
  expect(await client.signOutClaude("id")).toEqual({ remoteRevoked: false });
  expect(sent.map(s => [s.method, s.url.replace("http://localhost:8000", "")])).toEqual([
    ["POST", "/v1/auth/claude/attempts"], ["GET", "/v1/auth/claude/registrations"],
    ["POST", "/v1/auth/claude/registrations/id%2Fa/models"],
    ["GET", "/v1/auth/claude/attempts/a%2Fb"], ["POST", "/v1/auth/claude/attempts/a%2Fb/cancel"],
    ["POST", "/v1/auth/claude/registrations/id/sign-out"],
  ]);
});

it("retains the backend-confirmed catalog window after a provider switch", async () => {
  const client = new HttpBackendClient({ baseUrl: "http://localhost:8000", fetchFn: async () =>
    new Response(JSON.stringify({ backend: "claude_subscription", model: "claude-sonnet-5-5", context_window: 1000000 })) });
  expect(await client.setProvider({ backend: "claude_subscription", model: "claude-sonnet-5-5" }))
    .toMatchObject({ contextWindow: 1000000 });
});
