import { useState } from "react";
import { Avatar } from "../shared/Avatar";
import { MarkdownContent } from "../shared/MarkdownContent";
import { ThinkingBlock } from "../shared/ThinkingBlock";
import { Icon } from "../Icon";

interface Props {
  content: string;
  thinkingLog?: string[];
}

/**
 * QA agent answer turn.
 * Matches .turn / .avatar / .turn-body / .copy in the hi-fi mockup.
 *
 * Uses ReactMarkdown for content with prose-ish Tailwind styling via arbitrary selectors.
 * Copy button fades in on hover, briefly flips to "Copied ✓" on click.
 */
export function QAMessage({ content, thinkingLog }: Props) {
  const [copyLabel, setCopyLabel] = useState<"Copy" | "Copied ✓">("Copy");

  function handleCopy() {
    navigator.clipboard.writeText(content).catch(() => {
      // Clipboard access may be denied in non-https contexts; fail silently.
    });
    setCopyLabel("Copied ✓");
    setTimeout(() => setCopyLabel("Copy"), 1200);
  }

  return (
    <div className="group relative flex gap-2.5 items-start">
      <Avatar />

      <div className="flex-1 min-w-0 flex flex-col gap-1.5">
        {thinkingLog && thinkingLog.length > 0 && (
          <ThinkingBlock entries={thinkingLog} />
        )}

        <MarkdownContent content={content} />
      </div>

      {/* Copy button — visible on group hover */}
      <button
        type="button"
        onClick={handleCopy}
        className={[
          "absolute top-0 right-0",
          "opacity-0 group-hover:opacity-100 transition-opacity duration-150",
          "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-text-3 cursor-pointer",
          "bg-surface-2 border border-border-strong",
          "hover:text-text hover:border-[var(--accent-brd)]",
        ].join(" ")}
        aria-label="Copy message"
      >
        <Icon name="copy" size={10} />
        {copyLabel}
      </button>
    </div>
  );
}
