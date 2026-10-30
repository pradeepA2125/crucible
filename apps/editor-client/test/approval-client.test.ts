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
  it("maps the sandbox status and sends the switch only when given", async () => {
    const sandbox = { enabled: true, available: false, reason: "bubblewrap is not installed" };
    expect((await client({ approval: { ...wire, sandbox } }).getConfig()).approval).toMatchObject({ sandbox });
    const noSandbox = (await client({ approval: { ...wire, sandbox: null } }).getConfig()).approval;
    expect(noSandbox).not.toHaveProperty("sandbox");
    const request = { autoAccept: false, shellPolicy: "ask" as const, resolveWaiting: false, expectedProcessId: "p", expectedVersion: 3 };
    let sent: unknown;
    const applied = await client({ ...wire, sandbox: { ...sandbox, enabled: false } }, 200, b => sent = b)
      .setApprovalMode({ ...request, sandbox: false });
    expect(sent).toMatchObject({ sandbox: false });
    expect(applied.sandbox?.enabled).toBe(false);
    await client(wire, 200, b => sent = b).setApprovalMode(request);
    expect(sent).not.toHaveProperty("sandbox");
  });
  it("reads and writes the workspace's sandbox hosts and folders", async () => {
    const view = { status: { enabled: true, available: true, reason: null }, default_hosts: ["registry.npmjs.org"],
      hosts: ["example.com"], folders: ["/data"] };
    const config = { status: view.status, defaultHosts: view.default_hosts, hosts: view.hosts, folders: view.folders };
    expect(await client(view).getSandbox()).toEqual(config);
    let sent: unknown;
    expect(await client(view, 200, b => sent = b).setSandboxHosts(["example.com"])).toEqual(config);
    expect(sent).toEqual({ hosts: ["example.com"] });
    expect(await client(view, 200, b => sent = b).setSandboxFolders(["/data"])).toEqual(config);
    expect(sent).toEqual({ folders: ["/data"] });
  });
  it("surfaces a refused host's reason", async () => {
    await expect(client({ detail: "not a bare host: https://x" }, 422).setSandboxHosts(["https://x"]))
      .rejects.toThrow("not a bare host: https://x");
  });
  it("maps the thread usage's auto_review line", async () => {
    const z = { requests: 0, input: 0, output: 0, cached: 0 };
    const review = { requests: 2, input: 900, output: 40, cached: 600 };
    const usage = await client({ total: z, main: z, agents: {}, teams: {}, auto_review: review })
      .getThreadUsage("t");
    expect(usage.autoReview).toEqual(review);
    const none = await client({ total: z, main: z, agents: {}, teams: {}, auto_review: null })
      .getThreadUsage("t");
    expect(none.autoReview).toBeNull();
  });
  it("accepts unsupported effort descriptor options", () => {
    expect(EffortSupportSchema.parse({ supported: [], unsupported: {}, control: {
      options: [{ value: "max", label: "Max", description: "", state: "unsupported" }], source: "catalog", default: null, reason: null,
    } }).control?.options[0].state).toBe("unsupported");
  });
});
