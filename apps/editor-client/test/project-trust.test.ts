import { describe, expect, test } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";

interface Sent { url: string; method: string; body: unknown }

function clientWith(responseBody: unknown, sent: Sent[] = [], status = 200) {
  return new HttpBackendClient({
    baseUrl: "http://localhost:8000",
    fetchFn: async (url, init) => {
      sent.push({
        url: String(url),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responseBody), {
        status, headers: { "content-type": "application/json" },
      });
    },
  });
}

const FILES = {
  files: [
    { kind: "agents_md", state: "untrusted", sha256: "ab", content: "use ⟦U+200B⟧pnpm",
      diff_base: "use npm" },
    { kind: "mcp", state: "absent", sha256: null, content: null, diff_base: null },
  ],
};

describe("project trust client", () => {
  test("parses project trust", async () => {
    const sent: Sent[] = [];
    const trust = await clientWith(FILES, sent).getProjectTrust();
    expect(sent[0].url).toBe("http://localhost:8000/v1/project-trust");
    expect(trust.files).toHaveLength(2);
    expect(trust.files[1]).toEqual({ kind: "mcp", state: "absent", sha256: null, content: null,
                                     diffBase: null });
  });

  test("maps snake_case to camelCase", async () => {
    const trust = await clientWith(FILES).getProjectTrust();
    expect(trust.files[0].diffBase).toBe("use npm");
    const sent: Sent[] = [];
    expect(await clientWith({ trusted: true }, sent).reportProjectFileSaved("skills", null))
      .toBe(true);
    expect(sent[0]).toMatchObject({ method: "POST", body: { kind: "skills", before_sha256: null } });
    expect(sent[0].url).toContain("/v1/project-trust/saved");
    const posted: Sent[] = [];
    await clientWith({ ok: true }, posted).trustProjectFile("agents_md", "ab");
    expect(posted[0]).toMatchObject({ method: "POST", body: { kind: "agents_md", sha256: "ab" } });
  });

  test("trust 409 surfaces detail", async () => {
    const client = clientWith(
      { detail: "AGENTS.md changed since you reviewed it; review it again." }, [], 409);
    await expect(client.trustProjectFile("agents_md", "old"))
      .rejects.toThrow("AGENTS.md changed since you reviewed it; review it again.");
  });

  test("new fields parse when present", async () => {
    const skills = await clientWith({ skills: [{ name: "a", description: "d", trusted: false }] })
      .listSkills("/ws");
    expect(skills[0].trusted).toBe(false);
    const mcp = await clientWith({ enabled: true, trusted: false, servers: [] }).listMcpServers();
    expect(mcp.trusted).toBe(false);
    const config = await clientWith({
      task_subsystem_enabled: false, chat_controller_enabled: true, memory_enabled: true,
      skills_enabled: true, mcp_enabled: true, injection_defense: true,
    }).getConfig();
    expect(config.injectionDefense).toBe(true);
  });

  test("older backend without fields still parses", async () => {
    const skills = await clientWith({ skills: [{ name: "a", description: "d" }] })
      .listSkills("/ws");
    expect(skills[0].trusted).toBe(true);
    const mcp = await clientWith({ enabled: true, servers: [] }).listMcpServers();
    expect(mcp.trusted).toBe(true);
    const config = await clientWith({
      task_subsystem_enabled: false, chat_controller_enabled: true, memory_enabled: true,
      skills_enabled: true, mcp_enabled: true,
    }).getConfig();
    expect(config.injectionDefense).toBe(false);
  });
});
