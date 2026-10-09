import { afterEach, describe, expect, it, vi } from "vitest";
import { FileReferenceCatalog } from "../src/file-references/catalog.js";
const file = (i: number) => ({ entry: { workspaceId: "w", workspaceLabel: "w", relativePath: `src/f${i}.ts` }, uri: `uri-${i}`, root: "root" });
function setup(enumerate = vi.fn(async () => [file(1)])) {
  let invalidate = () => {};
  const dispose = vi.fn();
  const reportFailure = vi.fn();
  const catalog = new FileReferenceCatalog({ enumerate, reportFailure,
    onInvalidate(listener: () => void) { invalidate = listener; return { dispose }; },
  });
  return { catalog, enumerate, reportFailure, dispose, invalidate: () => invalidate() };
}
afterEach(() => vi.useRealTimers());
describe("shared file-reference catalog", () => {
  it("opens from the current inventory without rescanning on each click", async () => {
    const s = setup();
    const initial = await s.catalog.current();
    expect(await s.catalog.current()).toBe(initial);
    expect(await s.catalog.current()).toBe(initial);
    expect(s.enumerate).toHaveBeenCalledTimes(1);
    s.invalidate();
    const fresh = await s.catalog.current();
    expect(fresh.revision).toBe(2);
    expect(s.enumerate).toHaveBeenCalledTimes(2);
    s.catalog.dispose();
  });
  it("shares concurrent refreshes without a 5000 file cap", async () => {
    const files = Array.from({ length: 5001 }, (_, i) => file(i));
    const s = setup(vi.fn(async () => files));
    const [a, b] = await Promise.all([s.catalog.refresh(), s.catalog.refresh()]);
    expect(a).toBe(b);
    expect(a.files).toHaveLength(5001);
    expect(s.enumerate).toHaveBeenCalledTimes(1);
    await s.catalog.refresh();
    expect(s.enumerate).toHaveBeenCalledTimes(2);
    s.catalog.dispose();
  });
  it("debounces create/delete/config/folder invalidations into one publication", async () => {
    vi.useFakeTimers();
    const s = setup();
    const publish = vi.fn();
    s.catalog.subscribe(publish);
    s.invalidate(); s.invalidate(); s.invalidate();
    await vi.advanceTimersByTimeAsync(99);
    expect(s.enumerate).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(s.enumerate).toHaveBeenCalledTimes(1);
    expect(publish).toHaveBeenCalledTimes(1);
    s.catalog.dispose();
  });
  it("discards an inventory invalidated while in flight and publishes only its replacement", async () => {
    let resolve!: (files: ReturnType<typeof file>[]) => void;
    const enumerate = vi.fn(async () => [file(2)]);
    enumerate.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    const s = setup(enumerate);
    const publish = vi.fn();
    s.catalog.subscribe(publish);
    const pending = s.catalog.refresh();
    s.invalidate();
    resolve([file(1)]);
    const snapshot = await pending;
    expect(snapshot.files).toEqual([file(2)]);
    expect(snapshot.revision).toBe(1);
    expect(publish).toHaveBeenCalledTimes(1);
    expect(enumerate).toHaveBeenCalledTimes(2);
    s.catalog.dispose();
  });
  it("clears stale entries and reports a failed inventory", async () => {
    const s = setup();
    await s.catalog.refresh();
    const error = new Error("search failed");
    s.enumerate.mockRejectedValueOnce(error);
    expect((await s.catalog.refresh()).files).toEqual([]);
    expect(s.reportFailure).toHaveBeenCalledWith(error);
    s.catalog.dispose();
  });
  it("explicit refresh consumes a pending debounce", async () => {
    vi.useFakeTimers();
    const s = setup();
    s.invalidate();
    await s.catalog.refresh();
    await vi.advanceTimersByTimeAsync(200);
    expect(s.enumerate).toHaveBeenCalledTimes(1);
    s.catalog.dispose();
  });
  it("unsubscribes listeners and cancels timers on disposal", async () => {
    vi.useFakeTimers();
    const s = setup();
    const publish = vi.fn();
    const subscription = s.catalog.subscribe(publish);
    subscription.dispose();
    await s.catalog.refresh();
    expect(publish).not.toHaveBeenCalled();
    s.invalidate();
    s.catalog.dispose();
    await vi.advanceTimersByTimeAsync(200);
    expect(s.dispose).toHaveBeenCalledTimes(1);
    expect(s.enumerate).toHaveBeenCalledTimes(1);
  });
  it("does not publish an inventory completing after disposal", async () => {
    let resolve!: (files: ReturnType<typeof file>[]) => void;
    const s = setup(vi.fn(() => new Promise<ReturnType<typeof file>[]>(r => { resolve = r; })));
    const publish = vi.fn();
    s.catalog.subscribe(publish);
    const pending = s.catalog.refresh();
    s.catalog.dispose();
    resolve([file(1)]);
    await pending;
    expect(publish).not.toHaveBeenCalled();
  });
});
