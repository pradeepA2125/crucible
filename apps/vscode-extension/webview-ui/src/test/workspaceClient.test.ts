import { afterEach, expect, it, vi } from "vitest";
import { createWorkspaceClient, FileRequestCancelled } from "../desktop/workspaceClient";
const host = (data: unknown) => window.dispatchEvent(new MessageEvent("message", { data, source: window, origin: "app://app" }));
afterEach(() => { vi.useRealTimers(); });
it("queues beyond three requests, correlates replies and settles cancellation", async () => {
  const send = vi.fn(), client = createWorkspaceClient(send);
  const requests = [0, 1, 2, 3].map(index => client.list(String(index))); expect(send).toHaveBeenCalledTimes(3);
  host({ type: "desktop/filesResult", requestId: "foreign", result: {} }); expect(send).toHaveBeenCalledTimes(3);
  for (let index = 0; index < 4; index++) { const request = send.mock.calls[index]![0]; host({ type: "desktop/filesResult", requestId: request.requestId, result: { path: String(index) } }); }
  expect(await Promise.all(requests)).toEqual([0, 1, 2, 3].map(index => ({ path: String(index) })));
  const old = client.search("old", false), oldResult = old.catch(error => error);
  const latest = client.search("new", true), latestResult = latest.catch(error => error);
  expect(await oldResult).toBeInstanceOf(FileRequestCancelled);
  const current = send.mock.calls.at(-1)![0]; host({ type: "desktop/filesResult", requestId: current.requestId, cancelled: true });
  expect(await latestResult).toBeInstanceOf(FileRequestCancelled); client.dispose();
});
it("retries host busy with backoff and times out or disposes with bounded cleanup", async () => {
  vi.useFakeTimers(); const send = vi.fn(), client = createWorkspaceClient(send);
  const request = client.preview("file"), result = request.catch(error => error);
  const first = send.mock.calls[0]![0]; host({ type: "desktop/filesResult", requestId: first.requestId, reason: "busy", error: "busy" });
  expect(send).toHaveBeenCalledTimes(1); await vi.advanceTimersByTimeAsync(200); expect(send).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(8000); expect(await result).toMatchObject({ message: expect.stringContaining("did not respond") });
  const pending = client.list(""), disposed = pending.catch(error => error); client.dispose(); expect(await disposed).toBeInstanceOf(FileRequestCancelled);
});

it("keeps a single native editor chooser pending beyond inspection timeout and cancels on disposal", async () => {
  vi.useFakeTimers(); const send = vi.fn(), client = createWorkspaceClient(send);
  const first = client.action!("desktop/filesChooseEditor"), repeated = client.action!("desktop/filesChooseEditor");
  expect(first).toBe(repeated); expect(send).toHaveBeenCalledTimes(1);
  let settled = false; void first.then(() => { settled = true; });
  await vi.advanceTimersByTimeAsync(60000); expect(settled).toBe(false);
  host({ type: "desktop/filesResult", requestId: send.mock.calls[0]![0].requestId, result: { message: "Editor selected." } });
  expect(await first).toEqual({ message: "Editor selected." });
  const pending = client.action!("desktop/filesChooseEditor").catch(error => error);
  client.dispose(); expect(await pending).toBeInstanceOf(FileRequestCancelled);
});

it("ignores foreign-source and wrong-origin acknowledgements without releasing occupancy", async () => {
  const send = vi.fn(), client = createWorkspaceClient(send), first = client.preview("a.ts"); let settled = false; void first.then(() => { settled = true; });
  const reply = { type: "desktop/filesResult", requestId: send.mock.calls[0]![0].requestId, result: { path: "a.ts", kind: "text", text: "legitimate" } };
  const frame = document.createElement("iframe"); document.body.append(frame);
  for (const options of [{ source: frame.contentWindow, origin: "app://app" }, { source: window, origin: "https://foreign.invalid" }, { source: null, origin: "app://app" }, { source: window, origin: "null" }]) window.dispatchEvent(new MessageEvent("message", { ...options, data: reply }));
  await Promise.resolve(); expect(settled).toBe(false); expect(send).toHaveBeenCalledTimes(1); host(reply); await expect(first).resolves.toMatchObject({ text: "legitimate" }); frame.remove(); client.dispose();
});
