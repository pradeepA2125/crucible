import { useLayoutEffect, useRef } from "react";
import { isPinnedToBottom } from "../shared/scroll-pinning";

/** Keeps a scroller at its newest entry while the reader is at the bottom; scrolling
 * up stops following until they come back (the ThinkingBlock rule). */
export function useFollowBottom(changeToken: string) {
  const ref = useRef<HTMLDivElement | null>(null);
  const pinned = useRef(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [changeToken]);
  function onScroll() {
    const el = ref.current;
    if (!el) return;
    pinned.current = isPinnedToBottom({
      scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
    });
  }
  return { ref, onScroll };
}
