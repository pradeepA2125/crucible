import { describe, expect, it } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client";
import { EffortSupportSchema } from "../src/contracts/task-contracts";
const wire = { supported: true, auto_accept: false, shell_policy: "ask", version: 3, owned: true, process_id: "p" };
const client = (payload: unknown, status = 200, capture?: (body: unknown) => void) => new HttpBackendClient({
  baseUrl: "http://test", fetchFn: async (_url, init) => {
    if (init?.body) capture?.(JSON.parse(String(init.body)));
    return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
  },
});
describe("approval client", () => {
  it("maps approval state in config and live, and sends CAS on selection", async () => {
    const config = await client({ approval: wire, shell_policy: "ask" }).getConfig();
    expect(config.approval).toMatchObject({ supported: true, processId: "p", version: 3, autoAccept: false });
    expect((await client({ approval: wire }).getThreadLiveState("t")).approval).toEqual(config.approval);
    let body: unknown;
    const result = await client({ ...wire, auto_resolved_edits: 2, background: 1 }, 200, b => body = b).setApprovalMode({
      autoAccept: false, shellPolicy: "ask", resolveWaiting: true, expectedProcessId: "p", expectedVersion: 3,
    });
    expect(body).toMatchObject({ expected_process_id: "p", expected_version: 3, resolve_waiting: true });
    expect(result).toMatchObject({ processId: "p", autoResolvedEdits: 2, background: 1 });
  });
  it.each(["message", "mode", "clarify", "team"])("preserves structured admission errors for %s", async kind => {
    let body: unknown;
    const c = client({ detail: { code: "approval_not_confirmed", process_id: "new" } }, 409, b => body = b);
    const op = async () => {
      if (kind === "message") await c.sendChatMessage("t", "hi", undefined, { expectedProcessId: "p" });
      if (kind === "mode") for await (const _ of c.postModeDecision("t", "edit", "p")) { /* drain */ }
      if (kind === "clarify") for await (const _ of c.postClarifyDecision("t", "yes", "p")) { /* drain */ }
      if (kind === "team") await c.decideTeamPlan("t", "g", "approve", undefined, "p");
    };
    await expect(op()).rejects.toMatchObject({ status: 409, detail: { code: "approval_not_confirmed" } });
    expect(body).toHaveProperty("expected_process_id", "p");
  });
  it("parses shell_policy auto and the shell_policies capability", async () => {
    const auto = { ...wire, auto_accept: true, shell_policy: "auto", shell_policies: ["ask", "allow_all", "auto"] };
    const config = await client({ approval: auto, shell_policy: "auto" }).getConfig();
    expect(config.approval).toMatchObject({ shellPolicy: "auto", shellPolicies: ["ask", "allow_all", "auto"] });
    let body: unknown;
    await client(auto, 200, b => body = b).setApprovalMode({
      autoAccept: true, shellPolicy: "auto", resolveWaiting: true, expectedProcessId: "p", expectedVersion: 3,
    });
    expect(body).toMatchObject({ shell_policy: "auto" });
  });
  it("accepts unsupported effort descriptor options", () => {
    expect(EffortSupportSchema.parse({ supported: [], unsupported: {}, control: {
      options: [{ value: "max", label: "Max", description: "", state: "unsupported" }], source: "catalog", default: null, reason: null,
    } }).control?.options[0].state).toBe("unsupported");
  });
});
