import ReactMarkdown from "react-markdown";

interface Props {
  content: string;
}

/**
 * Markdown answer body inside a left-aligned agent box — the visual mirror of
 * the user's right-aligned bubble (.ubub), with prose-ish Tailwind styling via
 * arbitrary selectors. Shared by QAMessage and AgentRow so a finished agent
 * message renders the same whether or not the turn carried tool pills.
 */
export function MarkdownContent({ content }: Props) {
  return (
    <div
      style={{
        background: "linear-gradient(180deg, var(--color-surface-2), var(--color-surface))",
        border: "1px solid var(--color-border-strong)",
        boxShadow: "inset 0 1px 0 var(--hairline)",
        borderRadius: "4px 12px 12px 12px",
      }}
      className={[
        "self-start px-3 py-2",
        "text-xs text-text-2 leading-relaxed",
        // Inline code
        "[&_code]:mono [&_code]:text-code [&_code]:bg-surface-2 [&_code]:px-1 [&_code]:rounded",
        // Pre blocks
        "[&_pre]:mono [&_pre]:bg-surface-2 [&_pre]:rounded [&_pre]:p-2 [&_pre]:overflow-x-auto",
        // Paragraphs
        "[&_p]:mb-2 [&_p:last-child]:mb-0",
        // Lists
        "[&_ul]:list-disc [&_ul]:pl-4 [&_ul]:mb-2",
        "[&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:mb-2",
      ].join(" ")}
    >
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  );
}
