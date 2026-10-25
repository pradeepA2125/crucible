import { FileQueueFull } from "./workspaceClient";
import { useEffect, useRef } from "react";
import type { WorkspaceFileVersion } from "../../../../desktop/src/workspace-revision";
export interface FreshnessPreview { path: string; revision?: string; }
export interface FreshnessPort { metadata?(paths: string[]): Promise<{ entries: WorkspaceFileVersion[] }>; }
/** Bounded metadata polling covers external/background edits without loading their contents. */
export function usePreviewFreshness(port: FreshnessPort | undefined, previews: FreshnessPreview[], visible: boolean, changed: (paths: string[]) => void, unavailable: () => void) {
  const current = useRef({ previews, changed, unavailable }); current.current = { previews, changed, unavailable };
  useEffect(() => {
    if (!visible || !port?.metadata) return;
    let live = true, pending = false, last = -Infinity, failed = false;
    const probe = async () => {
      if (pending || document.visibilityState === "hidden" || Date.now() - last < 5000) return;
      const captured = current.current.previews.filter(value => value.revision).slice(0, 12);
      if (!captured.length) return;
      last = Date.now(); pending = true;
      try {
        const result = await port.metadata!(captured.map(value => value.path));
        if (!live) return; failed = false;
        const paths = captured.filter(before => current.current.previews.some(now => now.path === before.path && now.revision === before.revision)
          && result.entries.some(after => after.path === before.path && (after.reason || after.revision !== before.revision))).map(value => value.path);
        if (paths.length) current.current.changed(paths);
      } catch (error) {
        if (error instanceof FileQueueFull) return;
        if (live && !failed) { failed = true; current.current.unavailable(); }
      } finally { pending = false; }
    };
    const focused = () => { void probe(); };
    const timer = setInterval(focused, 5000);
    window.addEventListener("focus", focused); document.addEventListener("visibilitychange", focused);
    return () => { live = false; clearInterval(timer); window.removeEventListener("focus", focused); document.removeEventListener("visibilitychange", focused); };
  }, [port, visible]);
}
