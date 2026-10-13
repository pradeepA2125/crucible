import { useEffect, useLayoutEffect, useState, type RefObject } from "react";
export function popoverPosition(anchorLeft: number, viewportWidth: number, desiredWidth: number) {
  const width = Math.max(0, Math.min(desiredWidth, viewportWidth - 16));
  return { width, left: Math.max(8 - anchorLeft, Math.min(0, viewportWidth - 8 - anchorLeft - width)) };
}
/** Keep a composer popover inside its webview, including narrow sidebars. */
export function usePopover(root: RefObject<HTMLElement>, open: boolean, close: () => void, desiredWidth = 320, restoreFocus = true) {
  const [position, setPosition] = useState({ left: 0, width: desiredWidth });
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = root.current?.getBoundingClientRect();
      if (!anchor) return;
      setPosition(popoverPosition(anchor.left, window.innerWidth, desiredWidth));
    };
    place(); window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [root, open, desiredWidth]);
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) close(); };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") { close(); if (restoreFocus) root.current?.querySelector<HTMLButtonElement>("button")?.focus(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [root, open, close, restoreFocus]);
  return position;
}
