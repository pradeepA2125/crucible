import { afterEach, expect, it, vi } from "vitest";
import { createBaselineReadOperations } from "../src/baseline-read-operation.js";
import { createGitReadOperations } from "../src/git-read-operation.js";
const deferred = <T>() => { let resolve!: (value: T) => void, reject!: (error: unknown) => void; const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; };
afterEach(() => vi.useRealTimers());
it('retains four physical HTTP slots after cancellation across different reader instances', async () => {
  const reads = Array.from({length:4}, () => ({ controller: new AbortController(), entered: deferred<AbortSignal>(), work: deferred<string>() }));
  const tasks = reads.map(item => createBaselineReadOperations()(30000, {signal:item.controller.signal}, op => op.http(signal => {item.entered.resolve(signal);return item.work.promise;})).catch(error => error));
  await Promise.all(reads.map(item => item.entered.promise)); reads.forEach(item => item.controller.abort());
  for (const task of tasks) expect(await task).toMatchObject({reason:'cancelled'});
  expect((await reads[0]!.entered.promise).aborted).toBe(true);
  const extra = vi.fn(async () => 'extra'); await expect(createBaselineReadOperations()(1000, {}, op => op.http(extra))).rejects.toMatchObject({reason:'busy'}); expect(extra).not.toHaveBeenCalled();
  reads[0]!.work.resolve('late'); await new Promise(resolve => setImmediate(resolve));
  await expect(createBaselineReadOperations()(1000, {}, op => op.http(extra))).resolves.toBe('extra');
  reads.slice(1).forEach(item => item.work.reject(Error('late failure'))); await new Promise(resolve => setImmediate(resolve));
});
it('shares filesystem occupancy with Git and frees it only when the physical work settles', async () => {
  const blocks = Array.from({length:4}, () => deferred<string>()), entered = deferred<void>(), controller = new AbortController(); let count = 0;
  const old = createGitReadOperations()(30000, {signal:controller.signal}, async op => {
    await Promise.all(blocks.map(block => op.file(() => {if (++count===4) entered.resolve();return block.promise;}))); return 'old';
  }, async () => Buffer.alloc(0)).catch(error => error);
  await entered.promise; controller.abort(); expect(await old).toMatchObject({reason:'cancelled'});
  const work = vi.fn(async () => 'read'); await expect(createBaselineReadOperations()(1000, {}, op => op.file(work))).rejects.toMatchObject({reason:'busy'}); expect(work).not.toHaveBeenCalled();
  blocks.forEach(block => block.resolve('late')); await new Promise(resolve => setImmediate(resolve));
  await expect(createBaselineReadOperations()(1000, {}, op => op.file(work))).resolves.toBe('read');
});
it('deadline rejects promptly, reaches fetch signal and prevents late tasks from admitting more reads', async () => {
  vi.useFakeTimers({toFake:["setTimeout","clearTimeout","performance"]}); const entered=deferred<AbortSignal>(), stalled=deferred<string>(), after = vi.fn(async () => 'next');
  const pending=createBaselineReadOperations()(1000, {}, async op => { const value=await op.http(signal => {entered.resolve(signal);return stalled.promise;}); await op.http(after);return value; });
  const rejected=expect(pending).rejects.toMatchObject({reason:'timeout'}); const signal=await entered.promise;
  await vi.advanceTimersByTimeAsync(1001); await rejected; expect(signal.aborted).toBe(true);
  stalled.resolve('late'); await vi.advanceTimersByTimeAsync(0); expect(after).not.toHaveBeenCalled();
});
it('pre-abort and earlier external deadline do no work, and ordinary failures release admission', async () => {
  const operation=createBaselineReadOperations(), controller=new AbortController(), task=vi.fn(async () => 'never');controller.abort();
  await expect(operation(1000, {signal:controller.signal}, task)).rejects.toMatchObject({reason:'cancelled'});
  await expect(operation(1000, {deadline:Date.now()-1}, task)).rejects.toMatchObject({reason:'timeout'});expect(task).not.toHaveBeenCalled();
  await expect(operation(1000, {}, op => op.http(async () => {throw Error('failed');}))).rejects.toThrow('failed');
  await expect(operation(1000, {}, op => op.http(async () => 'ok'))).resolves.toBe('ok');
});

it('rejects non-finite timing and clamps oversized finite durations to the product maximum', async () => {
  const perform=createBaselineReadOperations(), task=vi.fn(async () => 'never');
  for (const value of [Infinity, NaN, -1]) await expect(perform(value, {}, task)).rejects.toThrow(RangeError);
  await expect(perform(1000, {deadline:NaN}, task)).rejects.toThrow(RangeError); expect(task).not.toHaveBeenCalled();
  vi.useFakeTimers({toFake:['setTimeout','clearTimeout','performance']});
  const pending=perform(2 ** 32, {}, () => new Promise<string>(() => {})); const refused=expect(pending).rejects.toMatchObject({reason:'timeout'});
  await vi.advanceTimersByTimeAsync(30001); await refused;
});
