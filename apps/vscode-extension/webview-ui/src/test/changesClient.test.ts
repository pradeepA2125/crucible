import { afterEach, expect, it, vi } from "vitest";
import { createChangesClient, type ChangesPort } from "../desktop/changesClient";
const generation = "12345678-1234-4234-8234-123456789abc";
const scope = { state: "ready", generation, changes: ["a.ts", "b.ts", "c.ts"].map(path => ({ path, status: "M" })),
  uncompared: [], partial: false, rawBytes: true, warnings: ["Raw bytes"] };
const clients: ChangesPort[] = [];
afterEach(() => { clients.splice(0).forEach(client => client.dispose()); vi.useRealTimers(); });
const event = (data: unknown) => window.dispatchEvent(new MessageEvent("message", { data, source: window, origin: "app://app" }));
const project = (projectId = "p") => event({ type: "desktop/project", name: "Project", projectId });
const setup = () => { const send = vi.fn(); const client = createChangesClient(send); clients.push(client); return { send, client }; };
const respond = (send: ReturnType<typeof vi.fn>, index: number, outcome: unknown, projectId = "p") => {
  const request = send.mock.calls[index]![0]; event({ type: "desktop/gitResult", projectId, requestId: request.requestId,
    operation: request.type === "desktop/gitScope" ? "scope" : "diff", outcome });
};
const ready = async (client: ChangesPort, send: ReturnType<typeof vi.fn>) => {
  project(); const pending = client.scope(); respond(send, send.mock.calls.length - 1, { status: "ok", result: scope }); await pending;
};
it("is lazy, waits for project identity and coalesces an explicit scope request", async () => {
  const { client, send } = setup(); expect(send).not.toHaveBeenCalled();
  await expect(client.scope()).rejects.toMatchObject({ reason: "project" });
  project(); expect(send).not.toHaveBeenCalled();
  const first = client.scope(), same = client.scope(); expect(same).toBe(first); expect(send).toHaveBeenCalledTimes(1);
  respond(send, 0, { status: "ok", result: scope }); await expect(first).resolves.toEqual(scope);
  await expect(client.diff(generation, "../outside")).rejects.toMatchObject({ reason: "unsupported" });
  await expect(client.diff(generation, "unsupported:name")).rejects.toMatchObject({ reason: "unsupported" });
  await expect(client.diff("HEAD", "a.ts")).rejects.toMatchObject({ reason: "stale" });
  expect(send).toHaveBeenCalledTimes(1);
});
it("sends only the newest queued diff and joins a repeated running file", async () => {
  const { client, send } = setup(); await ready(client, send);
  const first = client.diff(generation, "a.ts"), discarded = client.diff(generation, "b.ts");
  const rejected = expect(discarded).rejects.toMatchObject({ reason: "superseded" });
  const latest = client.diff(generation, "c.ts"); await rejected; expect(send).toHaveBeenCalledTimes(2);
  expect(client.diff(generation, "c.ts")).toBe(latest);
  respond(send, 1, { status: "ok", result: { kind: "binary" } }); await expect(first).resolves.toEqual({ kind: "binary" });
  expect(send).toHaveBeenCalledTimes(3); expect(send.mock.calls[2]![0].path).toBe("c.ts");
  respond(send, 2, { status: "ok", result: { kind: "too-large" } }); await latest;
  const again = client.diff(generation, "a.ts"), queued = client.diff(generation, "b.ts");
  const superseded = expect(queued).rejects.toMatchObject({ reason: "superseded" });
  expect(client.diff(generation, "a.ts")).toBe(again); await superseded;
  respond(send, 3, { status: "ok", result: { kind: "binary" } }); await again; expect(send).toHaveBeenCalledTimes(4);
});
it("holds a timed-out lane until its late acknowledgement and never revives expired bytes", async () => {
  vi.useFakeTimers(); const { client, send } = setup(); await ready(client, send);
  const first = client.diff(generation, "a.ts"), queued = client.diff(generation, "b.ts");
  const timeout = expect(first).rejects.toMatchObject({ reason: "timeout" });
  await vi.advanceTimersByTimeAsync(45001); await timeout; expect(send).toHaveBeenCalledTimes(2);
  respond(send, 1, { status: "ok", result: { kind: "binary" } }); expect(send).toHaveBeenCalledTimes(3);
  respond(send, 2, { status: "ok", result: { kind: "too-large" } }); await expect(queued).resolves.toEqual({ kind: "too-large" });
  // A duplicate old reply is no longer an active request.
  respond(send, 1, { status: "ok", result: { kind: "binary" } }); expect(send).toHaveBeenCalledTimes(3);
});
it("fails with bounded recovery instead of cascading queued requests after a lost acknowledgement", async () => {
  vi.useFakeTimers(); const { client, send } = setup(); await ready(client, send);
  const first = client.diff(generation, "a.ts"), queued = client.diff(generation, "b.ts");
  const timeout = expect(first).rejects.toMatchObject({ reason: "timeout" }), unavailable = expect(queued).rejects.toMatchObject({ reason: "unavailable" });
  await vi.advanceTimersByTimeAsync(60001); await timeout; await unavailable;
  await expect(client.scope()).rejects.toMatchObject({ reason: "unavailable" }); expect(send).toHaveBeenCalledTimes(2);
});
it("clears project jobs and generations before any old comparison can reach the next project", async () => {
  const { client, send } = setup(); await ready(client, send); const changed = vi.fn(); client.onProjectChange(changed);
  const old = client.diff(generation, "a.ts"), cancelled = expect(old).rejects.toMatchObject({ reason: "cancelled" });
  project("q"); await cancelled; expect(changed).toHaveBeenCalledWith("q");
  respond(send, 1, { status: "ok", result: { kind: "binary" } }, "p");
  await expect(client.diff(generation, "a.ts")).rejects.toMatchObject({ reason: "stale" }); expect(send).toHaveBeenCalledTimes(2);
  const next = client.scope(); respond(send, 2, { status: "ok", result: scope }, "q"); await next; expect(client.project()).toBe("q");
});
it("invalidates identity immediately on a cross-project reply and waits for an authoritative project event", async () => {
  const { client, send } = setup(); await ready(client, send); const pending = client.diff(generation, "a.ts");
  const cancelled = expect(pending).rejects.toMatchObject({ reason: "cancelled" });
  respond(send, 1, { status: "cancelled", message: "Project changed" }, "q"); await cancelled; expect(client.project()).toBeNull();
  await expect(client.scope()).rejects.toMatchObject({ reason: "project" }); expect(send).toHaveBeenCalledTimes(2);
});
it("rejects wrong-operation replies, preserves explicit busy and performs no automatic retry", async () => {
  const { client, send } = setup(); project(); const pending = client.scope();
  const busy = expect(pending).rejects.toMatchObject({ reason: "busy" }); respond(send, 0, { status: "busy", message: "Still working" }); await busy;
  expect(send).toHaveBeenCalledTimes(1);
  const next = client.scope(), invalid = expect(next).rejects.toMatchObject({ reason: "invalid" });
  event({ type: "desktop/gitResult", projectId: "p", requestId: send.mock.calls[1]![0].requestId, operation: "diff", outcome: { status: "ok", result: { kind: "binary" } } });
  await invalid; await expect(client.scope()).rejects.toMatchObject({ reason: "invalid" });
});
it("does not cache a timed-out scope result and disposes pending work/listeners", async () => {
  vi.useFakeTimers(); const { client, send } = setup(); project(); const pending = client.scope();
  const timeout = expect(pending).rejects.toMatchObject({ reason: "timeout" }); await vi.advanceTimersByTimeAsync(45001); await timeout;
  respond(send, 0, { status: "ok", result: scope });
  await expect(client.diff(generation, "a.ts")).rejects.toMatchObject({ reason: "stale" });
  const next = client.scope(), cancelled = expect(next).rejects.toMatchObject({ reason: "cancelled" });
  const callback = vi.fn(); client.onProjectChange(callback); client.dispose(); await cancelled;
  project("q"); expect(callback).not.toHaveBeenCalled(); await expect(client.scope()).rejects.toMatchObject({ reason: "cancelled" });
});
it("keeps project comparisons across thread switches, runs both lanes, and refuses an evicted diff", async () => {
  const { client, send } = setup(); await ready(client, send);
  event({ type: "clearThread", threadId: "different" });
  const old = client.diff(generation, "a.ts"), stale = expect(old).rejects.toMatchObject({ reason: "stale" });
  const refresh = client.scope(); expect(send).toHaveBeenCalledTimes(3);
  respond(send, 2, { status: "ok", result: { ...scope, generation: "12345678-1234-4234-8234-123456789abd" } }); await refresh;
  const newer = client.scope(), latestGeneration = "12345678-1234-4234-8234-123456789abe";
  respond(send, 3, { status: "ok", result: { ...scope, generation: latestGeneration } }); await newer;
  respond(send, 1, { status: "ok", result: { kind: "binary" } }); await stale;
  const next = client.diff(latestGeneration, "a.ts"); respond(send, 4, { status: "ok", result: { kind: "binary" } }); await next;
  expect(client.project()).toBe("p");
});
it("ignores unknown replies and fails safely when the sender cannot dispatch", async () => {
  const { client, send } = setup(); project(); const pending = client.scope(); let settled = false; pending.then(() => { settled = true; });
  event({ type: "desktop/gitResult", projectId: "p", requestId: "unknown", operation: "scope", outcome: { status: "ok", result: scope } });
  await Promise.resolve(); expect(settled).toBe(false);
  respond(send, 0, { status: "ok", result: scope }); await pending;
  const brokenSend = vi.fn(() => { throw new Error("offline"); }), broken = createChangesClient(brokenSend); clients.push(broken); project();
  await expect(broken.scope()).rejects.toMatchObject({ reason: "unavailable" });
  await expect(broken.scope()).rejects.toMatchObject({ reason: "unavailable" }); expect(brokenSend).toHaveBeenCalledTimes(1);
});

it("rejects an evicted queued diff before any host read is dispatched", async () => {
  const { client, send } = setup(); await ready(client, send);
  const running = client.diff(generation, "a.ts"), queued = client.diff(generation, "b.ts");
  const old = expect(running).rejects.toMatchObject({ reason: "stale" }), pending = expect(queued).rejects.toMatchObject({ reason: "stale" });
  for (const next of ["12345678-1234-4234-8234-123456789abd", "12345678-1234-4234-8234-123456789abe"]) {
    const refresh = client.scope(); respond(send, send.mock.calls.length - 1, { status: "ok", result: { ...scope, generation: next } }); await refresh;
  }
  respond(send, 1, { status: "ok", result: { kind: "binary" } }); await old; await pending;
  expect(send).toHaveBeenCalledTimes(4);
});

it("ignores forged project/results before correlation and admits the same legitimate reply", async () => {
  const { client, send } = setup(); project(); const changed = vi.fn(); client.onProjectChange(changed);
  const pending = client.scope(); let settled = false; void pending.then(() => { settled = true; });
  const reply = { type: "desktop/gitResult", requestId: send.mock.calls[0]![0].requestId, projectId: "p", operation: "scope", outcome: { status: "ok", result: scope } };
  const frame = document.createElement("iframe"); document.body.append(frame);
  for (const options of [{ source: frame.contentWindow, origin: "app://app" }, { source: window, origin: "https://foreign.invalid" }, { source: null, origin: "app://app" }, { source: window, origin: "null" }]) {
    window.dispatchEvent(new MessageEvent("message", { ...options, data: { type: "desktop/project", projectId: "foreign" } }));
    window.dispatchEvent(new MessageEvent("message", { ...options, data: reply }));
  }
  await Promise.resolve(); expect(settled).toBe(false); expect(client.project()).toBe("p"); expect(changed).not.toHaveBeenCalled(); expect(send).toHaveBeenCalledTimes(1);
  event(reply); await expect(pending).resolves.toEqual(scope); frame.remove();
});
it("preserves too-large wording without automatic retry and admits the next explicit comparison", async () => {
  const { client, send } = setup(); project(); const pending = client.scope(), failure = expect(pending).rejects.toMatchObject({ reason: "too-large", message: "This comparison exceeds the 16 MiB reply limit." });
  respond(send, 0, { status: "too-large", message: "This comparison exceeds the 16 MiB reply limit." }); await failure; expect(send).toHaveBeenCalledTimes(1);
  const retry = client.scope(); respond(send, 1, { status: "ok", result: scope }); await retry; expect(send).toHaveBeenCalledTimes(2);
});

it("fails closed with specific copy when an authenticated host envelope itself is oversized", async () => {
  const { client, send } = setup(); project(); const pending = client.scope(), failure = expect(pending).rejects.toMatchObject({ reason: "too-large", message: expect.stringContaining("16 MiB") });
  const path = ("folder".repeat(16) + "/").repeat(6);
  respond(send, 0, { status: "ok", result: { ...scope, changes: Array(40000).fill({ path, status: "M" }) } }); await failure;
  await expect(client.scope()).rejects.toMatchObject({ reason: "too-large" }); expect(send).toHaveBeenCalledTimes(1);
});
