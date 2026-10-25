import { memo, useMemo, type ReactNode } from "react";
import ReactMarkdown, { type Components, type Options } from "react-markdown";
import { vscode } from "../vscodeApi";
import { MARKDOWN_PLUGINS } from "../components/shared/MarkdownContent";
import { useFileReferenceIndex } from "./FileReferenceContext";
import { remarkFileReferences } from "./remarkFileReferences";

const components: Components = {
  table: ({ children, ...props }: { children?: ReactNode }) => (
    <div className="overflow-x-auto mb-4"><table {...props}>{children}</table></div>
  ),
  a: ({ node, ...props }) => {
    const reference = node?.properties?.["data-file-reference"];
    if (typeof reference !== "string") return <a {...props} />;
    const activate = (event: React.MouseEvent<HTMLAnchorElement> | React.KeyboardEvent<HTMLAnchorElement>) => {
      event.preventDefault();
      vscode.postMessage({ type: "openFileReference", reference });
    };
    return <a
      {...props}
      href="#workspace-file-reference"
      title={`Open workspace file ${reference}`}
      data-file-reference={reference}
      className="text-accent-ink underline decoration-accent-ink/50 underline-offset-2 hover:decoration-accent-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-ink"
      onClick={activate}
      onKeyDown={event => { if (event.key === "Enter" || event.key === " ") activate(event); }}
    />;
  },
};

/** Shared message-only Markdown renderer with live, exact file-reference links.
 *  Memoized on content: re-parsing is the expensive part of a render, and the streaming
 *  bubble re-renders on every reasoning delta while its answer text is unchanged. */
export const MessageMarkdown = memo(function MessageMarkdown({ content, overrides, disableFileReferences }: { content: string; overrides?: Components; disableFileReferences?: boolean }) {
  const index = useFileReferenceIndex();
  const plugins = useMemo(() => [...MARKDOWN_PLUGINS, [remarkFileReferences, { index }]] as Options["remarkPlugins"], [index]);
  return <ReactMarkdown remarkPlugins={disableFileReferences ? MARKDOWN_PLUGINS : plugins} components={overrides ? { ...components, ...overrides } : components}>
    {content}
  </ReactMarkdown>;
});
