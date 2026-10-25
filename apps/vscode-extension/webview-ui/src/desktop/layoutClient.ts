import { normalizeLayout, type DesktopLayout } from "../../../../desktop/src/layout";
export interface LayoutPort { read(): Promise<DesktopLayout>; save(layout: DesktopLayout): Promise<void>; onFlush?(callback: () => Promise<void>): () => void; }
export function createLayoutClient(send: (message: unknown) => void, target: Window = window): LayoutPort & { flush(): Promise<void> } {
  let flush: (() => Promise<void>) | undefined;
  const request = (type: string, layout?: DesktopLayout): Promise<DesktopLayout> => new Promise((resolve, reject) => {
    const requestId = crypto.randomUUID();
    const cleanup = () => { clearTimeout(timer); target.removeEventListener("message", receive); };
    const receive = (event: MessageEvent) => {
      if (event.data?.type !== "desktop/layoutResult" || event.data.requestId !== requestId) return;
      cleanup();
      if (event.data.error) reject(new Error(String(event.data.error)));
      else if (!event.data.layout) reject(new Error("Layout preferences are unavailable. Retry saving the layout."));
      else resolve(normalizeLayout(event.data.layout));
    };
    const timer = setTimeout(() => { cleanup(); reject(new Error("Layout could not be saved. Retry after the app reconnects.")); }, 4000);
    target.addEventListener("message", receive);
    try { send({ type, requestId, ...(layout ? { layout } : {}) }); }
    catch (error) { cleanup(); reject(error); }
  });
  return { onFlush: callback => { flush = callback; return () => { if (flush === callback) flush = undefined; }; }, flush: async () => {
      // Cosmetic preferences must not hold navigation/quit or the content flush.
      let timer: ReturnType<typeof setTimeout> | undefined;
      try { await Promise.race([flush?.(), new Promise<void>(resolve => { timer = setTimeout(resolve, 250); })]); }
      catch (error) { console.warn("Layout was not saved; conversation navigation will continue.", error); }
      finally { clearTimeout(timer); }
    },
    read: () => request("desktop/layoutRead"), save: async layout => { await request("desktop/layoutSave", layout); } };
}
