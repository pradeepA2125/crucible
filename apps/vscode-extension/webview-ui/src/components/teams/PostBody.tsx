import ReactMarkdown from "react-markdown";
import { MARKDOWN_COMPONENTS, MARKDOWN_PLUGINS, MARKDOWN_TABLE_CLASSES } from "../shared/MarkdownContent";

/** Markdown for team posts and reports — the chat's renderer without its bubble frame. */
export function PostBody({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={[
      "min-w-0 text-[12px] leading-[1.5] text-text break-words",
      "[&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_ul]:pl-4 [&_ol]:pl-4 [&_ul]:list-disc [&_ol]:list-decimal",
      "[&_h1]:text-[12px] [&_h2]:text-[12px] [&_h3]:text-[11px] [&_h4]:text-[11px] [&_h1]:font-semibold [&_h2]:font-semibold",
      "[&_h3]:uppercase [&_h4]:uppercase [&_h3]:tracking-[.06em] [&_h4]:tracking-[.06em] [&_h3]:text-text-2 [&_h4]:text-text-2",
      "[&_code]:font-mono [&_code]:text-[11px] [&_code]:text-code",
      ...MARKDOWN_TABLE_CLASSES, className,
    ].join(" ")}>
      <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} components={MARKDOWN_COMPONENTS}>{text}</ReactMarkdown>
    </div>
  );
}
