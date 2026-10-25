import { useEffect, useRef, type RefObject } from "react";
const SELECTOR = 'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])';

/** Entry, dynamic Tab trapping, Escape and return-to-opener for a mounted modal. */
export function useModalFocus(root: RefObject<HTMLElement>, close: () => void, returnTo?: RefObject<HTMLElement>, enabled = true) {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const dialog = root.current;
    if (!dialog || !enabled) return;
    const opener = returnTo?.current ?? document.activeElement as HTMLElement | null;
    const controls = () => [...dialog.querySelectorAll<HTMLElement>(SELECTOR)].filter(el => {
      if (el.tabIndex < 0 || el.closest('[hidden], [aria-hidden="true"]')) return false;
      for (let node: HTMLElement | null = el; node && dialog.contains(node); node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.display === "none" || style.visibility === "hidden") return false;
      }
      return true;
    });
    (controls()[0] ?? dialog).focus();
    const onKey = (event: KeyboardEvent) => {
      const modals = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
      if (modals[modals.length - 1] !== dialog) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeRef.current(); return; }
      if (event.key !== "Tab") return;
      const items = controls();
      const index = items.indexOf(document.activeElement as HTMLElement);
      if (!items.length) { event.preventDefault(); dialog.focus(); }
      else if (event.shiftKey && index <= 0) { event.preventDefault(); items[items.length - 1].focus(); }
      else if (!event.shiftKey && (index === items.length - 1 || index < 0)) { event.preventDefault(); items[0].focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => { document.removeEventListener("keydown", onKey, true); if (opener?.isConnected) opener.focus(); };
  }, [root, returnTo, enabled]);
}
