import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Props {
  content: string;
}

/**
 * A table is the one block that can be wider than the chat panel, which is narrow
 * and resizable. Scroll it inside its own box so the transcript itself never gains
 * a horizontal scrollbar.
 */
export const MARKDOWN_COMPONENTS = {
  table: ({ children, ...props }: { children?: ReactNode }) => (
    <div className="overflow-x-auto mb-2">
      <table {...props}>{children}</table>
    </div>
  ),
};

/**
 * GFM extras the model routinely emits and CommonMark does not cover: tables,
 * strikethrough, task lists, bare autolinks. Without this plugin react-markdown
 * renders a pipe table as a literal `| a | b |` paragraph.
 */
export const MARKDOWN_PLUGINS = [remarkGfm];

/** Table styling, shared with PlanCard so both markdown surfaces match. */
export const MARKDOWN_TABLE_CLASSES = [
  "[&_table]:w-full [&_table]:border-collapse [&_table]:text-[11px]",
  "[&_th]:border [&_th]:border-border-strong [&_th]:bg-surface-2 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold [&_th]:text-text",
  "[&_td]:border [&_td]:border-border-strong [&_td]:px-2 [&_td]:py-1 [&_td]:align-top",
];

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
        // Tables (GFM)
        ...MARKDOWN_TABLE_CLASSES,
      ].join(" ")}
    >
      <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} components={MARKDOWN_COMPONENTS}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
