import { MarkdownContent } from "../shared/MarkdownContent";
import { ThinkingBlock } from "../shared/ThinkingBlock";
import { CopyButton } from "../shared/CopyButton";

interface Props {
  content: string;
  thinkingLog?: string[];
}

/**
 * QA agent answer turn.
 * Matches .turn / .avatar / .turn-body / .copy in the hi-fi mockup.
 *
 * Uses ReactMarkdown for content with prose-ish Tailwind styling via arbitrary selectors.
 * Copy lives below the answer, outside the prose layout.
 */
export function QAMessage({ content, thinkingLog }: Props) {
  return (
    <div className="group/message flex min-w-0 flex-col items-stretch">
      <div className="flex-1 min-w-0 flex flex-col gap-1.5">
        {thinkingLog && thinkingLog.length > 0 && (
          <ThinkingBlock entries={thinkingLog} />
        )}

        <MarkdownContent content={content} />
      </div>

      {content.trim() && <div className="mt-1 flex opacity-0 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100 [@media(hover:none)]:opacity-100">
        <CopyButton text={content} ariaLabel="Copy message" />
      </div>}
    </div>
  );
}
