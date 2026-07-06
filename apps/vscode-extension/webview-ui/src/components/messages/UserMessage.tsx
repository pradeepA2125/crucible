import { vscode } from "../../vscodeApi";

/**
 * Right-aligned user bubble.
 * Matches .ubub in the hi-fi mockup.
 *
 * Inline backtick spans rendered as <code> (mono, text-code) — no markdown engine.
 * mentionedFiles are the paths the composer's @ dropdown actually inserted for this
 * message (not a blind @-regex scan) — only those "@path" tokens render clickable.
 * Arbitrary border-radius matches the mockup's 12px 12px 4px 12px shape.
 */
export function UserMessage({
  content,
  mentionedFiles = [],
}: {
  content: string;
  mentionedFiles?: string[];
}) {
  const codeParts = content.split(/(`[^`]+`)/);

  function renderTextSegment(text: string, keyPrefix: string) {
    if (mentionedFiles.length === 0) return <span key={keyPrefix}>{text}</span>;
    const mentionTokens = mentionedFiles.map((p) => `@${p}`);
    const pattern = new RegExp(`(${mentionTokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`);
    const pieces = text.split(pattern);
    return (
      <span key={keyPrefix}>
        {pieces.map((piece, i) =>
          mentionTokens.includes(piece) ? (
            <span
              key={i}
              role="button"
              tabIndex={0}
              onClick={() => vscode.postMessage({ type: "openFile", path: piece.slice(1) })}
              style={{ color: "var(--color-accent)", cursor: "pointer", textDecoration: "underline" }}
            >
              {piece}
            </span>
          ) : (
            <span key={i}>{piece}</span>
          ),
        )}
      </span>
    );
  }

  return (
    <div
      className="self-end max-w-[86%] px-3 py-2 text-xs leading-relaxed text-text whitespace-pre-wrap break-words"
      style={{
        background: "linear-gradient(180deg, var(--color-surface-2), var(--color-surface))",
        border: "1px solid var(--color-border-strong)",
        boxShadow: "inset 0 1px 0 var(--hairline)",
        borderRadius: "12px 12px 4px 12px",
      }}
    >
      {codeParts.map((part, i) => {
        if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
          return (
            <code key={i} className="mono text-code">
              {part.slice(1, -1)}
            </code>
          );
        }
        return renderTextSegment(part, `seg-${i}`);
      })}
    </div>
  );
}
