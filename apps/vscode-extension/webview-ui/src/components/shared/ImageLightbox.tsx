import { useEffect } from "react";

import { Icon } from "../Icon";

interface Props {
  src: string;
  name: string;
  onClose: () => void;
}

/** Full-size view of an attached image: Escape, a click outside or × closes it. */
export function ImageLightbox({ src, name, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={name}
      className="scrim anim-pop fixed inset-0 z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <img
        src={src}
        alt={name}
        className="max-h-[88vh] max-w-[92vw] rounded-card object-contain"
        style={{ border: "1px solid var(--color-border-strong)", boxShadow: "var(--shadow-popover)" }}
        onClick={(e) => e.stopPropagation()}
      />
      <button
        type="button"
        aria-label="Close image"
        onClick={onClose}
        className="absolute right-4 top-4 flex h-7 w-7 items-center justify-center rounded-full text-text-2 hover:text-text"
        style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-border-strong)" }}
      >
        <Icon name="x" size={12} />
      </button>
    </div>
  );
}
