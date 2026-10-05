import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client";

const wireAgent = {
  name: "helper", description: "d", tools: null, disallowed_tools: ["run_command"],
  permission: "default", declared_permission: "acceptEdits", model: "inherit", max_turns: null,
  skills: [], source: "claude", path: "/ws/.claude/agents/helper.md", sha256: "ab",
  trust: "capped", active: true, warnings: ["w"], shadowed_by: null, persona: "p", content: "c",
};

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

describe("agent definition client", () => {
  it("lists and maps snake_case, keeping tools null distinct from []", async () => {
    const fetchFn = vi.fn().mockResolvedValue(ok({
      agents: [wireAgent, { ...wireAgent, name: "none", tools: [] }],
      skipped: [{ path: "/x.md", reason: "missing 'description' — skipped" }],
      available_tools: ["read_file", "edit"],
    }));
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const catalog = await c.listAgentDefinitions();
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/agents");
    expect(catalog.agents[0]).toMatchObject({ tools: null, disallowedTools: ["run_command"],
      declaredPermission: "acceptEdits", shadowedBy: null, trust: "capped" });
    expect(catalog.agents[1].tools).toEqual([]);
    expect(catalog.availableTools).toEqual(["read_file", "edit"]);
    expect(catalog.skipped[0].reason).toContain("description");
  });

  it("saves with snake_case fields and returns the mapped row", async () => {
    const fetchFn = vi.fn().mockResolvedValue(ok({ agent: { ...wireAgent, source: "crucible" } }));
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const row = await c.saveAgentDefinition("new name", {
      description: "d", persona: "p", tools: null, disallowedTools: [], permission: "plan",
      model: "inherit", maxTurns: 30, skills: [], renameFrom: "old" });
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/agents/new%20name");
    const init = fetchFn.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({
      description: "d", persona: "p", tools: null, disallowed_tools: [], permission: "plan",
      model: "inherit", max_turns: 30, skills: [], rename_from: "old" });
    expect(row.source).toBe("crucible");
  });

  it("surfaces the backend's detail on failure", async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: false, status: 409, statusText: "Conflict",
      json: async () => ({ detail: "the file changed since you reviewed it" }) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const err = await c.trustAgentDefinition("/p.md", "ab").catch((e: unknown) => e) as Error & { status?: number };
    expect(err.message).toBe("the file changed since you reviewed it");
    expect(err.status).toBe(409);
    expect(JSON.parse(String((fetchFn.mock.calls[0][1] as RequestInit).body)))
      .toEqual({ path: "/p.md", sha256: "ab" });
  });

  it("deletes", async () => {
    const fetchFn = vi.fn().mockResolvedValue(ok({ ok: true }));
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    await c.deleteAgentDefinition("helper");
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/agents/helper");
    expect((fetchFn.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});
