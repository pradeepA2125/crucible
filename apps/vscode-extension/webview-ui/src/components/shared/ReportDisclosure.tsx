import { useId, useState, type ReactNode } from "react";
import { MarkdownContent } from "./MarkdownContent";

export function isLongReport(text: string): boolean {
  return Array.from(text.trim()).length > 420 || text.trim().split("\n").length > 6;
}

/** Long reports start with an actual excerpt, rather than clipping a full
 * markdown tree (which would leave hidden links and controls keyboard reachable).
 * Expansion restores the exact report through the surface's existing renderer. */
export function ReportDisclosure({ text, label, renderContent = content => <MarkdownContent content={content} /> }: {
  text: string; label: string; renderContent?: (content: string) => ReactNode;
}) {
  const [choice, setChoice] = useState({ text, open: false });
  const bodyId = useId();
  const open = choice.text === text && choice.open;
  const long = isLongReport(text);
  const excerpt = Array.from(text.trimStart().split("\n").slice(0, 4).join("\n")).slice(0, 320).join("").trimEnd() + "…";
  return (
    <div data-testid="report-disclosure" className="grid min-w-0 gap-2">
      <div id={bodyId} className="min-w-0">{renderContent(long && !open ? excerpt : text)}</div>
      {long && <button type="button" aria-expanded={open} aria-controls={bodyId}
        aria-label={`${open ? "Show less" : "Show more"} of ${label}`}
        onClick={() => setChoice({ text, open: !open })}
        className="justify-self-start rounded-control text-caption text-accent-ink hover:underline">
        {open ? "Show less" : "Show more"}
      </button>}
    </div>
  );
}
