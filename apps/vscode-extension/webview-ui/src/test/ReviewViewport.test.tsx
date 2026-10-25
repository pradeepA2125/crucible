import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { useReviewViewport } from "../desktop/useReviewViewport";
import { ReviewFileMenu } from "../desktop/ReviewFileMenu";
let observer: { callback: IntersectionObserverCallback; elements: Set<Element> };
class Observer {
  elements = new Set<Element>();
  constructor(public callback: IntersectionObserverCallback) { observer = this; }
  observe = (element: Element) => this.elements.add(element); unobserve = (element: Element) => this.elements.delete(element); disconnect = () => this.elements.clear();
}
afterEach(() => vi.unstubAllGlobals());
const paths = ['a.ts','b.ts','c.ts'];
function fixture(load: (path: string) => Promise<void>) {
  vi.stubGlobal('IntersectionObserver', Observer); const choose = vi.fn(), highlight = vi.fn(), loaded = new Set(['a.ts']);
  const trackedLoad = async (path: string) => { await load(path); loaded.add(path); };
  function View({ identity = 'gen1' }: { identity?: string }) {
    const [selected, setSelected] = useState('a.ts');
    const viewport = useReviewViewport({ enabled: true, identity, paths, selected, choose: path => { choose(path); setSelected(path); }, highlight, load: trackedLoad, ready: path => loaded.has(path) });
    return <><button onClick={() => viewport.jump('c.ts')}>Jump c</button><div data-testid="viewport" ref={viewport.viewport} onScroll={viewport.scrolled}>{paths.map(path => <article key={path} data-review-path={path} ref={element => viewport.section(path, element)}>{path}</article>)}</div></>;
  }
  const ui = render(<View />);
  const visible = (...names: string[]) => act(() => observer.callback(names.map(name => ({ target: [...observer.elements].find(element => (element as HTMLElement).dataset.reviewPath === name)!, isIntersecting: true })) as IntersectionObserverEntry[], observer as unknown as IntersectionObserver));
  return { ...ui, visible, choose, highlight, View };
}
it('loads approaching sections one at a time and unmount stops queued dispatch', async () => {
  let release!: () => void; const load = vi.fn(async () => {}); load.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
  const ui = fixture(load); expect(load).not.toHaveBeenCalled(); ui.visible('a.ts','b.ts','c.ts'); expect(load).toHaveBeenCalledTimes(1); expect(load).toHaveBeenCalledWith('b.ts');
  ui.unmount(); await act(async () => release()); expect(load).toHaveBeenCalledTimes(1);
});
it('does not reload the same visible section and jumps only the shared viewport', async () => {
  const load = vi.fn(async () => {}), ui = fixture(load); ui.visible('b.ts','c.ts'); await waitFor(() => expect(load).toHaveBeenCalledTimes(2)); ui.visible('b.ts','c.ts'); expect(load).toHaveBeenCalledTimes(2);
  const root = screen.getByTestId('viewport'), c = screen.getByText('c.ts');
  root.getBoundingClientRect = () => ({ top: 10 } as DOMRect); c.getBoundingClientRect = () => ({ top: 230, bottom: 330 } as DOMRect);
  fireEvent.click(screen.getByRole('button', { name: 'Jump c' })); expect(root.scrollTop).toBe(220); expect(ui.choose).toHaveBeenLastCalledWith('c.ts');
});
it('a pending old generation cannot dispatch its old queued section after identity changes', async () => {
  let release!: () => void; const load = vi.fn(async () => {}); load.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
  const ui = fixture(load); ui.visible('b.ts','c.ts'); ui.rerender(<ui.View identity="gen2" />); await act(async () => release()); expect(load).toHaveBeenCalledTimes(1);
});
it('file menu copies the actual path, handles clipboard errors and omits nonexistent current files', async () => {
  const writeText = vi.fn(async () => {}); vi.stubGlobal('navigator', { clipboard: { writeText } });
  const ui = render(<ReviewFileMenu path="src/test.ts" />);
  expect(screen.queryByRole('button', { name: 'Open current file' })).toBeNull(); fireEvent.click(screen.getByText('Copy path')); await screen.findByText('Path copied.'); expect(writeText).toHaveBeenCalledWith('src/test.ts');
  writeText.mockRejectedValueOnce(Error('blocked')); fireEvent.click(screen.getByText('Copy path')); await screen.findByText('Could not copy this path.'); ui.unmount();
});

it('cancellation permits later visibility to enqueue again, and scrolling only highlights', async () => {
  const load = vi.fn(async () => {}); load.mockRejectedValueOnce(Error('superseded'));
  const ui = fixture(load); ui.visible('b.ts'); await act(async () => {}); expect(load).toHaveBeenCalledTimes(1);
  ui.visible('b.ts'); await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  const root = screen.getByTestId('viewport'); root.getBoundingClientRect = () => ({ top: 10 } as DOMRect);
  for (const path of paths) screen.getByText(path).getBoundingClientRect = () => ({ top: path === 'b.ts' ? 20 : 200, bottom: path === 'b.ts' ? 100 : 300 } as DOMRect);
  fireEvent.scroll(root); expect(ui.highlight).toHaveBeenCalledWith('b.ts'); expect(ui.choose).not.toHaveBeenCalled(); expect(load).toHaveBeenCalledTimes(2);
});
it('opening a generation aligns its selected section without scrolling the page', () => {
  const load = vi.fn(async () => {}), ui = fixture(load), root = screen.getByTestId('viewport');
  root.getBoundingClientRect = () => ({ top: 10 } as DOMRect);
  screen.getByText('a.ts').getBoundingClientRect = () => ({ top: 210, bottom: 310 } as DOMRect);
  ui.rerender(<ui.View identity="next-generation" />); expect(root.scrollTop).toBe(200); expect(ui.choose).not.toHaveBeenCalled();
});
