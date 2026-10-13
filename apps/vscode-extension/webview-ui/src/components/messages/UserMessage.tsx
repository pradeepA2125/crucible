import { useState } from "react";

import { vscode } from "../../vscodeApi";
import { ImageLightbox } from "../shared/ImageLightbox";

const PREVIEWABLE = /^data:image\/(png|jpeg|webp|gif|bmp|tiff);base64,/;

/**
 * Right-aligned user bubble.
 * Matches .ubub in the hi-fi mockup.
 *
 * Inline backtick spans rendered as <code> (mono, text-code) — no markdown engine.
 * mentionedFiles are the paths the composer's @ dropdown actually inserted for this
 * message (not a blind @-regex scan) — only those "@path" tokens render clickable.
 * Accent-filled bubble with the shared composer radius.
 * Attached images sit above the bubble (as in Claude and Codex): one image as a
 * preview, several as square tiles; a click opens the full image.
 */
export function UserMessage({
  content,
  mentionedFiles = [],
  images = [],
  onRewind,
  queued = false,
}: {
  content: string;
  mentionedFiles?: string[];
  images?: { name: string; dataUrl?: string }[];
  /** Accepted by a running notice turn, not yet taken by it (spec v2 §5.3). */
  queued?: boolean;
  /** Absent when the message predates rewind (no id) or a turn is running. */
  onRewind?: () => void;
}) {
  const codeParts = content.split(/(`[^`]+`)/);
  const [open, setOpen] = useState<{ src: string; name: string } | null>(null);
  const single = images.length === 1;

  function renderTextSegment(text: string, keyPrefix: string) {
    if (mentionedFiles.length === 0) return <span key={keyPrefix}>{text}</span>;
    const mentionTokens = mentionedFiles.map((p) => `@${p}`);
    const pattern = new RegExp(`(${mentionTokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`);
    const pieces = text.split(pattern);
    return (
      <span key={keyPrefix}>
        {pieces.map((piece, i) =>
          mentionTokens.includes(piece) ? (
            <button
              key={i}
              type="button"
              onClick={() => vscode.postMessage({ type: "openFile", path: piece.slice(1) })}
              className="inline cursor-pointer border-0 bg-transparent p-0 font-[inherit]"
              style={{ color: "var(--on-accent)", textDecoration: "underline" }}
            >
              {piece}
            </button>
          ) : (
            <span key={i}>{piece}</span>
          ),
        )}
      </span>
    );
  }

  return (
    <div className="group relative flex max-w-[80%] flex-col items-end gap-1.5 self-end">
      {onRewind && (
        <button
          className="absolute -left-6 top-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 transition-opacity focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--focus-ring)]"
          title="Rewind to here"
          aria-label="Rewind to here"
          onClick={onRewind}
          style={{ color: "var(--color-text-2)" }}
        >
          ↺
        </button>
      )}
      {images.length > 0 && (
        <div data-testid="message-images" className="flex flex-wrap justify-end gap-1.5">
          {images.map((image, i) => image.dataUrl && PREVIEWABLE.test(image.dataUrl) ? (
            <button key={i} type="button" title={image.name} aria-label={`Open ${image.name}`}
              onClick={() => setOpen({ src: image.dataUrl!, name: image.name })}
              className="overflow-hidden rounded-card transition-opacity hover:opacity-90"
              style={{ border: "1px solid var(--color-border-strong)", background: "var(--color-surface-2)" }}>
              <img src={image.dataUrl} alt={image.name}
                className={single ? "block max-h-56 max-w-[260px] object-contain" : "block h-24 w-24 object-cover"} />
            </button>
          ) : (
            <span key={i} title={image.name}
              className="flex h-24 w-24 items-center justify-center rounded-card p-2 text-center text-caption leading-snug text-text-3"
              style={{ border: "1px dashed var(--color-border-strong)", background: "var(--color-surface-2)" }}>
              {image.name}: image preview unavailable
            </span>
          ))}
        </div>
      )}
      {(content.trim() || queued) && (
        <div
          data-testid="user-bubble"
          className="max-w-full rounded-composer px-3 py-2 text-body leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]"
          style={{ background: "var(--accent-fill)", color: "var(--on-accent)" }}
        >
          {codeParts.map((part, i) => {
            if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
              return (
                <code key={i} className="mono rounded-control px-1"
                  style={{ background: "var(--user-code-bg)" }}>
                  {part.slice(1, -1)}
                </code>
              );
            }
            return renderTextSegment(part, `seg-${i}`);
          })}
          {queued && (
            <div data-testid="queued-tag" className="mt-1 text-caption">
              Queued — the agent picks this up at its next step
            </div>
          )}
        </div>
      )}
      {open && <ImageLightbox src={open.src} name={open.name} onClose={() => setOpen(null)} />}
    </div>
  );
}
