import { useCallback, useEffect, useRef, type RefObject } from "react";
/** All sections share the parent's single scrolling viewport. No document-level scrolls. */
export function useReviewViewport({ enabled, identity, paths, selected, choose, highlight, load, ready }: {
  enabled: boolean; identity: string; paths: string[]; selected?: string; choose(path: string): void;
  highlight?: (path: string) => void; load?: (path: string) => Promise<void>; ready?: (path: string) => boolean;
}): { viewport: RefObject<HTMLDivElement>; section(path: string, element: HTMLElement | null): void; jump(path: string): void; scrolled(): void } {
  const viewport = useRef<HTMLDivElement>(null), elements = useRef(new Map<string, HTMLElement>()), serial = useRef(0), queue = useRef<string[]>([]), busy = useRef(false), seen = useRef(new Set<string>());
  const latest = useRef({ choose, highlight, load, ready, selected, paths }); latest.current = { choose, highlight, load, ready, selected, paths };
  const observer = useRef<IntersectionObserver>();
  const pump = useCallback(async function drain() {
    if (busy.current) return; busy.current = true; const captured = serial.current;
    try { while (queue.current.length && captured === serial.current) {
      const path = queue.current.shift()!; if (latest.current.ready?.(path)) continue;
      try { await latest.current.load?.(path); } catch { /* The owning model exposes failures; only visibility or explicit navigation may retry. */ }
      finally { if (captured === serial.current) seen.current.delete(path); }
    } } finally { busy.current = false; if (queue.current.length) void drain(); }
  }, []);
  const enqueue = useCallback((path: string) => {
    if (!latest.current.load || latest.current.ready?.(path) || seen.current.has(path)) return;
    seen.current.add(path); queue.current.push(path); void pump();
  }, [pump]);
  useEffect(() => {
    serial.current++; queue.current = []; seen.current.clear();
    if (!enabled || !viewport.current) return;
    const root = viewport.current, initial = latest.current.selected && elements.current.get(latest.current.selected);
    if (initial) root.scrollTop += initial.getBoundingClientRect().top - root.getBoundingClientRect().top;
    if (!load) return;
    if (typeof IntersectionObserver !== "function") { const first = latest.current.selected ?? paths[0]; if (first) enqueue(first); return; }
    const current = new IntersectionObserver(entries => { for (const entry of entries) if (entry.isIntersecting) { const path = (entry.target as HTMLElement).dataset.reviewPath; if (path) enqueue(path); } }, { root: viewport.current, rootMargin: "160px 0px", threshold: 0 });
    observer.current = current; for (const element of elements.current.values()) current.observe(element);
    return () => { current.disconnect(); observer.current = undefined; serial.current++; queue.current = []; };
  }, [enabled, identity, enqueue]);
  const section = (path: string, element: HTMLElement | null) => { const previous = elements.current.get(path); if (previous) observer.current?.unobserve(previous); if (!element) { elements.current.delete(path); return; } elements.current.set(path, element); observer.current?.observe(element); };
  const jump = (path: string) => {
    choose(path); if (!enabled) return;
    const root = viewport.current, element = elements.current.get(path); if (root && element) root.scrollTop = element.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop;
    enqueue(path);
  };
  const scrolled = () => {
    if (!enabled || !viewport.current) return;
    const top = viewport.current.getBoundingClientRect().top + 45;
    const path = latest.current.paths.find(path => { const rect = elements.current.get(path)?.getBoundingClientRect(); return rect && rect.top <= top && rect.bottom > top; });
    if (path && path !== latest.current.selected) (latest.current.highlight ?? latest.current.choose)(path);
  };
  return { viewport, section, jump, scrolled };
}
