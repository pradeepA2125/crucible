import { describe, expect, it, vi } from "vitest";
import { gateDecisionIdentity, type GateDecisionClaim } from "../src/contracts/task-contracts.js";
import { ChatAdmissionUnknownError, HttpBackendClient } from "../src/client/http-backend-client.js";
const claim: GateDecisionClaim = { version: 1, requestId: "request-1", processId: "process",
  workspacePath: "/project with space/中文", gateId: "gate", kind: "feedback", value: "original feedback" };
function wire(i: Awaited<ReturnType<typeof gateDecisionIdentity>>) {
  return { version: i.version, request_id: i.requestId, process_id: i.processId, workspace_path: i.workspacePath,
    gate_id: i.gateId, kind: i.kind, thread_id: i.threadId, payload_sha256: i.payloadSha256, status: "accepted" };
}
async function fixture(mutate: (v: Record<string, unknown>) => unknown = v => v, status = 200) {
  const identity = await gateDecisionIdentity("thread/a", claim);
  const fetch = vi.fn(async (input: string, init?: RequestInit) => {
    const u = new URL(input);
    const value = init?.method === "POST" ? wire(identity) : {
      version: 1, nonce: u.searchParams.get("nonce"), found: true, receipt: wire(identity),
    };
    return new Response(JSON.stringify(mutate(value)), { status });
  });
  return { identity, fetch, client: new HttpBackendClient({ baseUrl: "http://localhost", fetchFn: fetch, authToken: () => "test-token" }) };
}
describe("exact reactive gate claims", () => {
  it("matches Python's ASCII JSON hash vector over unnormalized original Unicode and controls", async () => {
    const identity = await gateDecisionIdentity("thread/a", { ...claim, value: 'é😀\x7f\u2028\\"\n\b' });
    expect(identity.payloadSha256).toBe("bf44292d6695e02a5b5521a41985871c032809411e7abe91bbde4bea5a6f63b5");
    expect((await gateDecisionIdentity("thread/a", { ...claim, value: " original feedback " })).payloadSha256).not.toBe((await gateDecisionIdentity("thread/a", claim)).payloadSha256);
  });
  it("serializes exact identity and original value, acknowledges only a complete matching receipt", async () => {
    const f = await fixture(); const admitted = vi.fn(); const signal = new AbortController().signal;
    expect(await f.client.claimChatGate("thread/a", claim, { signal, onAdmitted: admitted })).toEqual({ ...f.identity, status: "accepted" });
    expect(admitted).toHaveBeenCalledTimes(1);
    const [url, init] = f.fetch.mock.calls[0]!;
    expect(url).toContain("thread%2Fa/gate-decision");
    expect(init).toMatchObject({ method: "POST", signal, cache: "no-store", headers: { authorization: "Bearer test-token" } });
    expect(JSON.parse(init!.body as string)).toEqual({ version: 1, request_id: claim.requestId, process_id: claim.processId,
      workspace_path: claim.workspacePath, gate_id: claim.gateId, kind: claim.kind, value: claim.value });
  });
  it.each(["process_id", "request_id", "workspace_path", "thread_id", "gate_id", "kind", "payload_sha256", "version", "status", "extra"])("treats malformed or mismatched %s as unknown acceptance without retry", async key => {
    const f = await fixture(v => ({ ...v, [key]: "wrong" })); const admitted = vi.fn();
    await expect(f.client.claimChatGate("thread/a", claim, { onAdmitted: admitted })).rejects.toBeInstanceOf(ChatAdmissionUnknownError);
    expect(admitted).not.toHaveBeenCalled(); expect(f.fetch).toHaveBeenCalledTimes(1);
  });
  it("keeps explicit launch failure as accepted consumption and rejects missing support without legacy fallback", async () => {
    const f = await fixture(v => ({ ...v, status: "launch_failed" }));
    expect(await f.client.claimChatGate("thread/a", claim)).toMatchObject({ status: "launch_failed" });
    for (const status of [404, 409, 422]) {
      const old = await fixture(v => v, status);
      await expect(old.client.claimChatGate("thread/a", claim)).rejects.toMatchObject({ status });
      expect(old.fetch).toHaveBeenCalledTimes(1);
    }
    const bad = await fixture(v => v, 503);
    await expect(bad.client.claimChatGate("thread/a", claim)).rejects.toBeInstanceOf(ChatAdmissionUnknownError);
  });
  it("binds nonce and all original receipt fields, including old process IDs; absence stays unknown", async () => {
    const f = await fixture(); const signal = new AbortController().signal;
    expect(await f.client.getGateDecisionReceipt(f.identity, signal)).toMatchObject({ found: true, receipt: { processId: "process" } });
    const [url, init] = f.fetch.mock.calls[0]!; const u = new URL(url);
    expect(u.searchParams.get("workspace_path")).toBe(claim.workspacePath);
    expect(u.searchParams.get("payload_sha256")).toBe(f.identity.payloadSha256);
    expect(init).toMatchObject({ cache: "no-store", signal });
    await f.client.getGateDecisionReceipt(f.identity);
    expect(new URL(f.fetch.mock.calls[1]![0]).searchParams.get("nonce")).not.toBe(u.searchParams.get("nonce"));
    const absent = await fixture(v => ({ ...v, found: false, receipt: null }));
    expect(await absent.client.getGateDecisionReceipt(absent.identity)).toMatchObject({ found: false, receipt: null });
    const stale = await fixture(v => ({ ...v, nonce: "prior" }));
    await expect(stale.client.getGateDecisionReceipt(stale.identity)).rejects.toThrow("Stale");
    const wrong = await fixture(v => ({ ...v, receipt: { ...(v.receipt as object), gate_id: "replacement" } }));
    await expect(wrong.client.getGateDecisionReceipt(wrong.identity)).rejects.toThrow("mismatch");
  });
  it("bounds incomplete successful bodies, physically cancels on abort and never acknowledges or retries", async () => {
    const abort = new AbortController(); const cancel = vi.fn(); const admitted = vi.fn();
    const fetch = vi.fn(async () => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('{"version":')); }, cancel })));
    const client = new HttpBackendClient({ baseUrl: "http://localhost", fetchFn: fetch });
    const pending = client.claimChatGate("thread/a", claim, { signal: abort.signal, onAdmitted: admitted });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1)); abort.abort();
    await expect(pending).rejects.toBeInstanceOf(ChatAdmissionUnknownError);
    expect(cancel).toHaveBeenCalledTimes(1); expect(admitted).not.toHaveBeenCalled();
  });
  it("bounds receipt bytes and timed reads rather than returning acceptance from a truncated body", async () => {
    const oversized = await fixture(() => "x".repeat(65537));
    await expect(oversized.client.claimChatGate("thread/a", claim)).rejects.toBeInstanceOf(ChatAdmissionUnknownError);
    const cancel = vi.fn(); const identity = (await fixture()).identity;
    const client = new HttpBackendClient({ baseUrl: "http://localhost", fetchFn: async () => new Response(new ReadableStream({ cancel })) });
    vi.useFakeTimers();
    try {
      const reading = client.getGateDecisionReceipt(identity);
      const checked = expect(reading).rejects.toThrow("timed out");
      await vi.advanceTimersByTimeAsync(15_001); await checked;
      expect(cancel).toHaveBeenCalledTimes(1);
    } finally { vi.useRealTimers(); }
  });
});
