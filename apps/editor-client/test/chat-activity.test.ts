import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";
function fixture(mutate: (value: Record<string, unknown>) => unknown = value => value, status = 200) {
  const fetch = vi.fn(async (input: string, _init?: RequestInit) => {
    const url = new URL(input);
    const body = mutate({ version: 1, supported: true, request_id: url.searchParams.get("request_id"),
      requested_workspace: url.searchParams.get("workspace"), process_id: "process", workspace_path: "/canonical",
      threads: [{ thread_id: "a", turn_active: false, agents_running: 0, sessions_running: 0 }] });
    return new Response(JSON.stringify(body), { status });
  });
  return { fetch, client: new HttpBackendClient({ baseUrl: "http://localhost", fetchFn: fetch }) };
}
describe("fresh reactive project activity", () => {
  it("encodes the project, binds a unique request echo, maps all required fields and passes cancellation/no-store", async () => {
    const f = fixture(); const signal = new AbortController().signal;
    const result = await f.client.getChatActivity("/project with space/中文", signal);
    expect(result).toMatchObject({ supported: true, processId: "process", workspacePath: "/canonical",
      requestedWorkspace: "/project with space/中文", threads: [{ threadId: "a", turnActive: false, agentsRunning: 0, sessionsRunning: 0 }] });
    expect(new URL(f.fetch.mock.calls[0]![0]).searchParams.get("request_id")).toBe(result.requestId);
    expect(f.fetch.mock.calls[0]![1]).toMatchObject({ signal, cache: "no-store" });
    const next = await f.client.getChatActivity("/project with space/中文"); expect(next.requestId).not.toBe(result.requestId);
  });
  it.each(["missing-turn", "missing-agents", "missing-sessions", "missing-process", "duplicate", "bad-count", "stale-nonce", "wrong-project", "wrong-version"])("rejects %s instead of inferring idle", async kind => {
    const f = fixture(value => {
      const threads = value.threads as Record<string, unknown>[];
      if (kind === "missing-turn") delete threads[0]!.turn_active;
      else if (kind === "missing-agents") delete threads[0]!.agents_running;
      else if (kind === "missing-sessions") delete threads[0]!.sessions_running;
      else if (kind === "missing-process") delete value.process_id;
      else if (kind === "duplicate") threads.push({ ...threads[0] });
      else if (kind === "bad-count") threads[0]!.agents_running = -1;
      else if (kind === "stale-nonce") value.request_id = "prior-request";
      else if (kind === "wrong-project") value.requested_workspace = "/other";
      else value.version = 2;
      return value;
    });
    await expect(f.client.getChatActivity("/project")).rejects.toThrow();
  });
  it("retains explicit unsupported evidence and treats only404 as legacy", async () => {
    const f = fixture(value => ({ version: value.version, request_id: value.request_id,
      requested_workspace: value.requested_workspace, supported: false, reason: "thread_limit" }));
    await expect(f.client.getChatActivity("/project")).resolves.toMatchObject({ supported: false, reason: "thread_limit" });
    await expect(fixture(value => value, 404).client.getChatActivity("/project")).resolves.toMatchObject({ supported: false, reason: "legacy_backend" });
    for (const status of [401, 403, 500]) await expect(fixture(value => value, status).client.getChatActivity("/project")).rejects.toMatchObject({ status });
  });
  it("aborts the actual pending read without retrying or returning idle", async () => {
    const abort = new AbortController();
    const fetch = vi.fn((_input: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init!.signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    const client = new HttpBackendClient({ baseUrl: "http://localhost", fetchFn: fetch });
    const reading = client.getChatActivity("/project", abort.signal); abort.abort();
    await expect(reading).rejects.toMatchObject({ name: "AbortError" }); expect(fetch).toHaveBeenCalledTimes(1);
  });
});
