import { describe, expect, test } from "vitest";
import { HttpBackendClient, ProviderAccessError } from "../src/client/http-backend-client.js";

interface Sent { url: string; method: string; body: unknown }

function clientWith(status: number, responseBody: unknown, sent: Sent[] = []) {
  return new HttpBackendClient({
    baseUrl: "http://127.0.0.1:8000",
    fetchFn: async (url, init) => {
      sent.push({
        url: String(url), method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responseBody), {
        status, headers: { "content-type": "application/json" } });
    },
  });
}

const ATTEMPT = {
  attempt_id: "att_1", state: "pending", registration_id: null, plan_enabled: null,
  first_plan_sign_in: false, reason: null, message: null,
};

describe("ChatGPT sign-in client", () => {
  test("startChatGPTSignIn posts the registration and maps the attempt", async () => {
    const sent: Sent[] = [];
    const res = await clientWith(200, { ...ATTEMPT, authorize_url: "https://auth/x" }, sent)
      .startChatGPTSignIn({ registrationId: "reg_aaaaaaaaaaaa", reconsent: true });
    expect(sent[0]).toMatchObject({ method: "POST", body: {
      registration_id: "reg_aaaaaaaaaaaa", reconsent: true } });
    expect(sent[0].url).toBe("http://127.0.0.1:8000/v1/auth/chatgpt/attempts");
    expect(res).toEqual({
      attemptId: "att_1", state: "pending", registrationId: null, planEnabled: null,
      firstPlanSignIn: false, reason: null, message: null, authorizeUrl: "https://auth/x" });
  });

  test("listChatGPTAccounts maps summaries", async () => {
    const res = await clientWith(200, { registrations: [{
      registration_id: "reg_aaaaaaaaaaaa", label: "a@x.com", email: "a@x.com", name: null,
      plan_enabled: true, signed_in: false }] }).listChatGPTAccounts();
    expect(res).toEqual([{ registrationId: "reg_aaaaaaaaaaaa", label: "a@x.com",
      email: "a@x.com", name: null, planEnabled: true, signedIn: false }]);
  });

  test("listChatGPTModels maps the catalog in server order", async () => {
    const res = await clientWith(200, { models: [
      { slug: "b", display_name: "B" }, { slug: "a", display_name: "A" }] })
      .listChatGPTModels("reg_aaaaaaaaaaaa");
    expect(res).toEqual([{ slug: "b", displayName: "B" }, { slug: "a", displayName: "A" }]);
  });

  test("an account refusal on the model catalog is a ProviderAccessError", async () => {
    const err = await clientWith(409, { detail: { kind: "session_invalid", message: "Sign in again" } })
      .listChatGPTModels("reg_aaaaaaaaaaaa").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProviderAccessError);
    expect(err).toMatchObject({ kind: "session_invalid", message: "Sign in again" });
  });

  test("signOutChatGPT reports whether revocation was confirmed", async () => {
    expect(await clientWith(200, { remote_revoked: false }).signOutChatGPT("reg_aaaaaaaaaaaa"))
      .toEqual({ remoteRevoked: false });
  });

  test("config and live state carry the plan flag and the access card", async () => {
    const config = await clientWith(200, {
      task_subsystem_enabled: false, chat_controller_enabled: true, memory_enabled: true,
      skills_enabled: false, mcp_enabled: false,
      provider: { backend: "chatgpt", model: "m", uses_chatgpt_plan: true },
    }).getConfig();
    expect(config.provider?.usesChatgptPlan).toBe(true);
    const live = await clientWith(200, {
      active_task_id: null, status: null, pending_gates: [], plan: null,
      provider_access: { kind: "usage_limit", message: "Usage limit reached.",
                         code: "subscription_sharing_usage_limit_exceeded", status: 429,
                         request_id: "req_1" },
    }).getThreadLiveState("t1");
    expect(live.providerAccess).toEqual({ kind: "usage_limit", message: "Usage limit reached.",
      code: "subscription_sharing_usage_limit_exceeded", status: 429, requestId: "req_1" });
  });
});
