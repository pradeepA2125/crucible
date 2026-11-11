/**
 * What the voice assistant said aloud: a spoken reply or a spoken summary of a finished
 * answer. Secondary to the written transcript (a quiet indented line with its own label),
 * so it never reads as a second answer. The label, not color, says what it is.
 */
export function SpokenLine({ label, content }: { label: string; content: string }) {
  return (
    <div
      data-testid="spoken-line"
      className="flex min-w-0 flex-col gap-0.5 border-l-2 py-0.5 pl-3 text-meta text-text-2"
      style={{ borderColor: "var(--color-border-strong)" }}
    >
      <span className="text-caption text-text-3">{label}</span>
      <p className="m-0 whitespace-pre-wrap [overflow-wrap:anywhere]">{content}</p>
    </div>
  );
}
