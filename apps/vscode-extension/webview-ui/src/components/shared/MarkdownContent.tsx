import type { Components } from "react-markdown";
import type { ReactNode } from "react";
import remarkGfm from "remark-gfm";
import { MessageMarkdown } from "../../file-references/MessageMarkdown";

interface Props {
  content: string;
  overrides?: Components;
  disableFileReferences?: boolean;
}

/**
 * A table is the one block that can be wider than the chat panel, which is narrow
 * and resizable. Scroll it inside its own box so the transcript itself never gains
 * a horizontal scrollbar.
 */
export const MARKDOWN_COMPONENTS = {
  table: ({ children, ...props }: { children?: ReactNode }) => (
    <div className="overflow-x-auto mb-4">
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
  "message-markdown",
  "min-w-0 max-w-full [overflow-wrap:anywhere]",
  "[&_pre]:min-w-0 [&_pre]:max-w-full [&_pre]:whitespace-pre-wrap",
];

/** Table styling, shared with PlanCard so both markdown surfaces match. */
export const MARKDOWN_TABLE_CLASSES = [
  "[&_table]:w-full [&_table]:border-collapse [&_table]:text-body [&_table]:leading-relaxed",
  "[&_th]:border-b [&_th]:border-border-strong [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-text",
  "[&_td]:border-b [&_td]:border-border [&_td]:px-3 [&_td]:py-2 [&_td]:align-top",
];

/**
 * An agent answer as plain prose (spec §2): no card, no border, no avatar. Prose size,
 * primary text, relaxed line height, `text-wrap: pretty`. Inline code uses the
 * theme accent; file references keep their accent links (MessageMarkdown). Shared by
 * QAMessage and AgentRow so an answer looks the same with or without tool steps.
 */
export function MarkdownContent({ content, overrides, disableFileReferences }: Props) {
  return (
    <div
      className={[
        "self-stretch",
        ...MARKDOWN_WRAP_CLASSES,
        "text-prose text-text leading-[1.65]",
        "[&_p]:[text-wrap:pretty]",
        // Inline code follows the shared message-markdown style in index.css.
        // Pre blocks
        "[&_pre]:mono [&_pre]:text-body [&_pre]:bg-[var(--surface-1)] [&_pre]:rounded-control [&_pre]:p-3 [&_pre]:my-4",
        // Paragraphs
        "[&_p]:mb-4 [&_p:last-child]:mb-0",
        // Lists
        "[&_ul]:list-disc [&_ul]:pl-5 [&_ul]:mb-4",
        "[&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:mb-4",
        "[&_li+li]:mt-1.5 [&_li>p]:mb-2 [&_li>ul]:mt-2 [&_li>ol]:mt-2 [&_li>ul]:mb-0 [&_li>ol]:mb-0",
        // Headings retain the type scale, using spacing and weight for hierarchy.
        "[&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold [&_h4]:font-semibold",
        "[&_:is(h1,h2,h3,h4)]:mt-6 [&_:is(h1,h2,h3,h4)]:mb-3 [&_>:first-child]:mt-0 [&_>:last-child]:mb-0",
        // Tables (GFM)
        ...MARKDOWN_TABLE_CLASSES,
      ].join(" ")}
    >
      <MessageMarkdown content={content} overrides={overrides} disableFileReferences={disableFileReferences} />
    </div>
  );
}
