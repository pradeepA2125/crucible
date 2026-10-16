import { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";

/** Copy the labelled content only; acknowledge success after the clipboard accepts it. */
export function CopyButton({ text, label = "Copy", ariaLabel = label }: {
  text: string; label?: string; ariaLabel?: string;
}) {
  const [status, setStatus] = useState<"idle" | "copying" | "copied" | "failed">("idle");
  const attempt = useRef(0);
  const reset = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => {
    setStatus("idle");
    attempt.current++;
    return () => { attempt.current++; clearTimeout(reset.current); };
  }, [text]);

  async function copy() {
    const current = attempt.current;
    clearTimeout(reset.current);
    setStatus("copying");
    let next: "copied" | "failed";
    try {
      await navigator.clipboard.writeText(text);
      next = "copied";
    } catch {
      next = "failed";
    }
    if (current !== attempt.current) return;
    setStatus(next);
    reset.current = setTimeout(() => setStatus("idle"), 1600);
  }

  return <button type="button" onClick={copy} disabled={status === "copying"}
    aria-label={ariaLabel} title={status === "failed" ? "Clipboard unavailable. Try again." : ariaLabel}
    className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-control px-1.5 py-0.5 text-caption font-normal normal-case tracking-normal text-text-3 hover:bg-surface-2 hover:text-text disabled:cursor-wait focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--focus-ring)]">
    <Icon name={status === "copied" ? "check" : "copy"} size={11} />
    <span aria-live="polite">{status === "copied" ? "Copied" : status === "failed" ? "Copy failed" : status === "copying" ? "Copying…" : label}</span>
  </button>;
}
