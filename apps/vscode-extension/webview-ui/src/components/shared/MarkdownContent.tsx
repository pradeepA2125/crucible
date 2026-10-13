import type { ReactNode } from "react";
import remarkGfm from "remark-gfm";
import { MessageMarkdown } from "../../file-references/MessageMarkdown";

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

/** Anywhere wrapping also reduces min-content width in nested grid/flex layouts. */
export const MARKDOWN_WRAP_CLASSES = [
  "min-w-0 max-w-full [overflow-wrap:anywhere]",
  "[&_pre]:min-w-0 [&_pre]:max-w-full [&_pre]:whitespace-pre-wrap",
];

/** Table styling, shared with PlanCard so both markdown surfaces match. */
export const MARKDOWN_TABLE_CLASSES = [
  "[&_table]:w-full [&_table]:border-collapse [&_table]:text-meta",
  "[&_th]:border [&_th]:border-border-strong [&_th]:bg-surface-2 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold [&_th]:text-text",
  "[&_td]:border [&_td]:border-border-strong [&_td]:px-2 [&_td]:py-1 [&_td]:align-top",
];

/**
 * An agent answer as plain prose (spec §2): no card, no border, no avatar. Prose size,
 * primary text, 1.6 line height, `text-wrap: pretty`. Inline code is a neutral chip on
 * surface-1; file references keep their accent links (MessageMarkdown). Shared by
 * QAMessage and AgentRow so an answer looks the same with or without tool steps.
 */
export function MarkdownContent({ content }: Props) {
  return (
    <div
      className={[
        "self-stretch",
        ...MARKDOWN_WRAP_CLASSES,
        "text-prose text-text leading-[1.6]",
        "[&_p]:[text-wrap:pretty]",
        // Inline code: a neutral chip (not code-coloured)
        "[&_:not(pre)>code]:mono [&_:not(pre)>code]:bg-[var(--surface-1)] [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:rounded-control",
        // Pre blocks
        "[&_pre]:mono [&_pre]:bg-[var(--surface-1)] [&_pre]:rounded-control [&_pre]:p-2",
        // Paragraphs
        "[&_p]:mb-2 [&_p:last-child]:mb-0",
        // Lists
        "[&_ul]:list-disc [&_ul]:pl-4 [&_ul]:mb-2",
        "[&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:mb-2",
        // Tables (GFM)
        ...MARKDOWN_TABLE_CLASSES,
      ].join(" ")}
    >
      <MessageMarkdown content={content} />
    </div>
  );
}
