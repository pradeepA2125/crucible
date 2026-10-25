import { useEffect, useRef, useState } from "react";
import { DEFAULT_LAYOUT, normalizeLayout, type DesktopLayout } from "../../../../desktop/src/layout";
import type { LayoutPort } from "./layoutClient";
export function useDesktopLayout(port?: LayoutPort) {
  const [layout, setLayout] = useState({ ...DEFAULT_LAYOUT }), [error, setError] = useState("");
  const latest = useRef(layout), changed = useRef(new Set<keyof DesktopLayout>());
  const commit = useRef(() => {});
  useEffect(() => {
    let live = true, loaded = !port, pending = false;
    let saving: Promise<void> | undefined, reading: Promise<void> | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fail = (reason: unknown) => { if (live) setError(reason instanceof Error ? reason.message : "Layout could not be saved."); };
    const load = (): Promise<void> => {
      if (!port || !live || loaded) return Promise.resolve();
      if (reading) return reading;
      reading = Promise.resolve().then(async () => {
        const stored = await port.read(); if (!live) return;
        const value = normalizeLayout(stored);
        for (const key of changed.current) Object.assign(value, { [key]: latest.current[key] });
        latest.current = value; setLayout(value); loaded = true;
      }).finally(() => { reading = undefined; });
      return reading;
    };
    const save = (): Promise<void> => {
      if (!port || !live) return Promise.resolve();
      if (saving) return saving.then(() => pending ? save() : undefined);
      if (!pending) return Promise.resolve();
      saving = Promise.resolve().then(async () => {
        try { await load(); while (pending && live) { pending = false; await port.save(latest.current); if (live) setError(""); } }
        catch (reason) { pending = true; throw reason; }
      }).finally(() => { saving = undefined; });
      return saving;
    };
    commit.current = () => { pending = true; clearTimeout(timer); timer = setTimeout(() => { void save().catch(fail); }, 150); };
    void load().then(() => { if (pending) void save().catch(fail); }, fail);
    const stopFlush = port?.onFlush?.(async () => { clearTimeout(timer); if (pending || saving) { try { await save(); } catch (reason) { fail(reason); throw reason; } } });
    return () => { live = false; clearTimeout(timer); stopFlush?.(); };
  }, [port]);
  const update = (patch: Partial<DesktopLayout>) => {
    for (const key of Object.keys(patch) as (keyof DesktopLayout)[]) changed.current.add(key);
    latest.current = normalizeLayout({ ...latest.current, ...patch }); setLayout(latest.current); commit.current();
  };
  return { layout, update, error, retry: () => commit.current() };
}
