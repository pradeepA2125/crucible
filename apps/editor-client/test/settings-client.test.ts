import { describe, expect, test } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";

interface Sent { url: string; method: string; body: unknown }

function clientWith(responseBody: unknown, sent: Sent[] = []) {
  return new HttpBackendClient({
    baseUrl: "http://localhost:8000",
    fetchFn: async (url, init) => {
      sent.push({
        url: String(url),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responseBody), {
        status: 200, headers: { "content-type": "application/json" },
      });
    },
  });
}

describe("settings client methods", () => {
  test("validateProvider posts body and parses result", async () => {
    const sent: Sent[] = [];
    const res = await clientWith({ ok: true, model: "m" }, sent)
      .validateProvider({ backend: "groq", credentials: { GROQ_API_KEY: "k" } });
    expect(res).toEqual({ ok: true, model: "m" });
    expect(sent[0].url).toContain("/v1/providers/validate");
    expect((sent[0].body as { backend: string }).backend).toBe("groq");
  });

  test("validateProvider maps json_mode to jsonMode and passes warning through", async () => {
    const res = await clientWith({
      ok: true, model: "m", json_mode: "json_object", warning: "degraded",
    }).validateProvider({ backend: "openai_compatible", model: "m" });
    expect(res).toEqual({ ok: true, model: "m", jsonMode: "json_object", warning: "degraded" });
  });

  test("validateProvider omits jsonMode/warning when the route omits them", async () => {
    const res = await clientWith({ ok: true, model: "m" })
      .validateProvider({ backend: "gemini", model: "m" });
    expect(res).toEqual({ ok: true, model: "m" });
    expect(res.jsonMode).toBeUndefined();
    expect(res.warning).toBeUndefined();
  });

  test("setProvider PUTs to /v1/config/provider", async () => {
    const sent: Sent[] = [];
    const res = await clientWith({ ok: true, backend: "groq", model: "m2" }, sent)
      .setProvider({ backend: "groq", model: "m2" });
    expect(res).toEqual({ backend: "groq", model: "m2" });
    expect(sent[0].method).toBe("PUT");
    expect(sent[0].url).toContain("/v1/config/provider");
  });

  test("listMcpServers maps snake_case to camelCase", async () => {
    const res = await clientWith({ enabled: true, servers: [{
      name: "web", transport: "stdio", enabled_in_file: true,
      state: "connected", detail: null, tool_count: 2 }] }).listMcpServers();
    expect(res.servers[0]).toEqual({
      name: "web", transport: "stdio", enabledInFile: true,
      state: "connected", detail: null, toolCount: 2 });
  });

  test("upsertMcpServer PUTs entry + disabled", async () => {
    const sent: Sent[] = [];
    await clientWith({ enabled: true, servers: [] }, sent)
      .upsertMcpServer("web", { command: "uv", enabled: true }, ["gh"]);
    expect(sent[0].url).toContain("/v1/mcp/servers/web");
    expect(sent[0].method).toBe("PUT");
    expect(sent[0].body).toEqual({
      entry: { command: "uv", enabled: true }, disabled: ["gh"] });
  });

  test("getConfig parses the provider report", async () => {
    const res = await clientWith({
      task_subsystem_enabled: false, chat_controller_enabled: true,
      memory_enabled: false, skills_enabled: true, mcp_enabled: true,
      provider: { backend: "gemini", model: "gemini-flash-latest" },
    }).getConfig();
    expect(res.provider).toEqual({
      backend: "gemini", model: "gemini-flash-latest", usesChatgptPlan: false });
  });

  test("deleteMcpServer and reconnectMcpServer send the disabled list", async () => {
    const sent: Sent[] = [];
    const client = clientWith({ enabled: true, servers: [] }, sent);
    await client.deleteMcpServer("web", ["a"]);
    await client.reconnectMcpServer("web", ["a", "b"]);
    expect(sent[0].method).toBe("DELETE");
    expect(sent[0].body).toEqual({ disabled: ["a"] });
    expect(sent[1].url).toContain("/v1/mcp/servers/web/reconnect");
    expect(sent[1].body).toEqual({ disabled: ["a", "b"] });
  });

  test("getConfig maps context_window to contextWindow", async () => {
    const res = await clientWith({
      task_subsystem_enabled: false, chat_controller_enabled: true,
      memory_enabled: true, skills_enabled: true, mcp_enabled: true,
      provider: { backend: "openai", model: "gpt-5", context_window: 200000 },
    }).getConfig();
    expect(res.provider?.contextWindow).toBe(200000);
  });

  test("getConfig tolerates a backend that reports no context_window", async () => {
    const res = await clientWith({
      task_subsystem_enabled: false, chat_controller_enabled: true,
      memory_enabled: true, skills_enabled: true, mcp_enabled: true,
      provider: { backend: "openai", model: "gpt-5" },
    }).getConfig();
    expect(res.provider?.contextWindow).toBeUndefined();
  });

  test("getConfig tolerates a null context_window", async () => {
    // The live backend always sends the key and it can genuinely be null (no
    // window configured for the process) — this must not throw a Zod error.
    const res = await clientWith({
      task_subsystem_enabled: false, chat_controller_enabled: true,
      memory_enabled: true, skills_enabled: true, mcp_enabled: true,
      provider: { backend: "openai", model: "gpt-5", context_window: null },
    }).getConfig();
    expect(res.provider?.contextWindow).toBeNull();
  });

  test("setProvider sends contextWindow as context_window", async () => {
    const sent: Sent[] = [];
    await clientWith({ ok: true, backend: "groq", model: "m2", context_window: 32768 }, sent)
      .setProvider({ backend: "groq", model: "m2", contextWindow: 32768 });
    expect((sent[0].body as { context_window: number }).context_window).toBe(32768);
  });

  test("setProvider omits context_window entirely when not supplied", async () => {
    /* Absent means "leave the window alone" on the route — a null would read as a
       value and is not the same thing. */
    const sent: Sent[] = [];
    await clientWith({ ok: true, backend: "groq", model: "m2" }, sent)
      .setProvider({ backend: "groq", model: "m2" });
    expect("context_window" in (sent[0].body as object)).toBe(false);
  });

  test("testContextWindow posts to the route and maps prompt_tokens", async () => {
    const sent: Sent[] = [];
    const res = await clientWith(
      { ok: true, recalled: false, prompt_tokens: 141234, exact: true }, sent,
    ).testContextWindow({ backend: "openai_compatible", contextWindow: 128000 });
    expect(sent[0].method).toBe("POST");
    expect(sent[0].url).toContain("/v1/providers/context-test");
    expect((sent[0].body as { context_window: number }).context_window).toBe(128000);
    expect(res).toEqual({ ok: true, recalled: false, promptTokens: 141234, exact: true });
  });

  test("testContextWindow keeps ok and recalled distinct on a failed call", async () => {
    const res = await clientWith({ ok: false, recalled: false, error: "429 rate limited" })
      .testContextWindow({ backend: "groq", contextWindow: 128000 });
    expect(res).toEqual({ ok: false, recalled: false, error: "429 rate limited" });
  });
});
