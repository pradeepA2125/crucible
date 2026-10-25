import { act, renderHook, } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useGitChanges } from "../desktop/useGitChanges";
import { ChangesInspectionError, createChangesClient, type ChangesPort } from "../desktop/changesClient";
import type { GitDiff, GitScope } from "../../../../desktop/src/workspace-git";
const generation = '12345678-1234-4234-8234-123456789abc';
const scope = (count = 3): GitScope => ({ state: 'ready', generation, head: 'a'.repeat(40), changes: Array.from({ length: count }, (_, n) => ({ path: `file-${n}.ts`, status: 'M' as const })), rawBytes: true, partial: false, warnings: [], uncompared: [] });
const text = (value = 'new'): GitDiff => ({ kind: 'text', unified: '@@ -1 +1 @@\n-old\n+' + value, additions: 1, deletions: 1, truncated: false, fingerprint: 'a'.repeat(64) });
function fixture(count = 3) {
  let changed: ((id: string|null) => void) | undefined;
  const port: ChangesPort = { project: () => 'p', onProjectChange: callback => { changed = callback; return () => {}; }, scope: vi.fn(async () => scope(count)), diff: vi.fn(async () => text()), dispose: vi.fn() };
  const hook = renderHook(() => useGitChanges(port)); return { ...hook, port, projectChanged: () => act(() => changed?.('q')) };
}
it('retains bounded complete details above the view and passive reads never change the chosen file', async () => {
  const { result, port } = fixture(); await act(async () => { await result.current.refresh(); });
  expect(result.current.details?.get('file-0.ts')?.result).toEqual(text());
  await act(async () => { await result.current.load?.('file-1.ts'); }); expect(result.current.selected).toBe('file-0.ts');
  act(() => result.current.select('file-1.ts')); expect(result.current.selected).toBe('file-1.ts'); expect(port.diff).toHaveBeenCalledTimes(2);
  await act(async () => { await result.current.load?.('file-1.ts'); }); expect(port.diff).toHaveBeenCalledTimes(2);
});
it('drops aggregate cached text at the rendered-line threshold and uses immediate single-file mode for large scopes', async () => {
  const { result, port } = fixture(50); vi.mocked(port.diff).mockResolvedValue(text('line\n'.repeat(800)));
  await act(async () => { await result.current.refresh(); for (let n=1;n<5;n++) await result.current.load?.(`file-${n}.ts`); });
  expect(result.current.large).toBe(true); expect(result.current.details?.size).toBe(0); expect(port.diff).toHaveBeenCalledTimes(4);
  expect(result.current.detail?.result).toBeDefined();
  vi.mocked(port.scope).mockResolvedValue(scope(51)); await act(async () => { await result.current.refresh(); }); expect(result.current.large).toBe(true);
  const count = vi.mocked(port.diff).mock.calls.length; await act(async () => { await result.current.load?.('file-2.ts'); }); expect(port.diff).toHaveBeenCalledTimes(count);
});
it('does not let a late passive read revive cache/marks after a project change or Refresh', async () => {
  const { result, port, projectChanged } = fixture(); await act(async () => { await result.current.refresh(); });
  let finish!: (value: GitDiff) => void; vi.mocked(port.diff).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  let pending!: Promise<void>; act(() => { pending = result.current.load!('file-1.ts'); }); projectChanged();
  await act(async () => { finish(text('late')); await pending; }); expect(result.current.details?.size).toBe(0); expect(result.current.result).toBeUndefined();
  expect(port.scope).toHaveBeenCalledTimes(1);
});

it('finishes a cached foreground read after selecting away and back without leaving a loading placeholder', async () => {
  const { result, port } = fixture(); await act(async () => { await result.current.refresh(); });
  let finish!: (value: GitDiff) => void;
  vi.mocked(port.diff).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  act(() => result.current.select('file-1.ts'));
  act(() => result.current.select('file-0.ts'));
  act(() => result.current.select('file-1.ts'));
  expect(port.diff).toHaveBeenCalledTimes(2);
  await act(async () => { finish(text('finished')); });
  expect(result.current.selected).toBe('file-1.ts');
  expect(result.current.detail?.result).toEqual(text('finished'));
  expect(result.current.details?.get('file-1.ts')?.loading).toBe(false);
});

it('recovers a superseded section with the real coalescing client, without an automatic retry', async () => {
  const send = vi.fn(), client = createChangesClient(send);
  const event = (data: unknown) => window.dispatchEvent(new MessageEvent('message', { data, source: window, origin: 'app://app' }));
  event({ type: 'desktop/project', projectId: 'p' });
  const respond = (index: number, value: unknown) => {
    const request = send.mock.calls[index]![0]; event({ type: 'desktop/gitResult', projectId: 'p', requestId: request.requestId, operation: request.type === 'desktop/gitScope' ? 'scope' : 'diff', outcome: { status: 'ok', result: value } });
  };
  const hook = renderHook(() => useGitChanges(client));
  let refresh!: Promise<void>;
  act(() => { refresh = hook.result.current.refresh(); });
  await act(async () => respond(0, scope(4)));
  await act(async () => { hook.result.current.select('file-2.ts'); hook.result.current.select('file-3.ts'); });
  expect(hook.result.current.details?.get('file-2.ts')).toBeUndefined(); expect(send).toHaveBeenCalledTimes(2);
  await act(async () => { respond(1, text('a')); await refresh; });
  expect(send.mock.calls[2]![0].path).toBe('file-3.ts');
  await act(async () => hook.result.current.select('file-2.ts'));
  await act(async () => respond(2, text('d')));
  expect(send.mock.calls[3]![0].path).toBe('file-2.ts');
  await act(async () => respond(3, text('c')));
  expect(hook.result.current.detail?.result).toEqual(text('c')); expect(hook.result.current.details?.get('file-2.ts')?.error).toBeUndefined();
  hook.unmount(); client.dispose();
});
it('scroll highlighting does not read; an explicit selection retries a genuine cached error', async () => {
  const { result, port } = fixture(); await act(async () => { await result.current.refresh(); });
  act(() => result.current.highlight?.('file-1.ts')); expect(result.current.selected).toBe('file-1.ts'); expect(port.diff).toHaveBeenCalledTimes(1);
  vi.mocked(port.diff).mockRejectedValueOnce(new ChangesInspectionError('busy', 'Earlier read finishing.'));
  await act(async () => { await result.current.load?.('file-1.ts'); }); expect(result.current.detail?.error).toBe('Earlier read finishing.');
  await act(async () => { await result.current.load?.('file-1.ts'); }); expect(port.diff).toHaveBeenCalledTimes(2);
  await act(async () => result.current.select('file-1.ts')); expect(port.diff).toHaveBeenCalledTimes(3); expect(result.current.detail?.result).toEqual(text());
});
