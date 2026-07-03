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
    expect(res.provider).toEqual({ backend: "gemini", model: "gemini-flash-latest" });
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
});
