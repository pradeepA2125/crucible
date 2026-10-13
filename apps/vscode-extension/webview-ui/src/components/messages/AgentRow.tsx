import { useState } from "react";
import type { ToolEventView } from "../../types";
import { MarkdownContent } from "../shared/MarkdownContent";
import { ThinkingBlock } from "../shared/ThinkingBlock";
import { ActivityLine } from "../activity/ActivityLine";
import { TeamLinks } from "../teams/TeamLinks";
import { Icon } from "../Icon";

interface Props {
  content: string;
  breadcrumb?: boolean;
  progress?: boolean;
  thinkingLog?: string[];
  toolEvents?: ToolEventView[];
  streaming?: boolean;
  streamingThinkingEntries?: string[];
  streamingThinkingChunk?: string;
  /** The live answer row has no thinking to show: the transient line carries it. */
  hideThinkingBlock?: boolean;
}

/**
 * Generic agent row — handles breadcrumbs, tool steps, streaming content.
 * Matches .turn / .crumb / .stream-line / .caret in the hi-fi mockup.
 *
 * breadcrumb: compact icon+text row, strips leading marker character.
 * progress: a non-terminal mid-turn narration note, rendered as answer prose
 *   (markdown, body size) so it reads as the agent talking between activity lines.
 * normal: plain text, optional streaming caret.
 * Copy button on hover (not while streaming).
 */
export function AgentRow({
  content,
  breadcrumb,
  progress,
  thinkingLog,
  toolEvents,
  streaming,
  streamingThinkingEntries,
  streamingThinkingChunk,
  hideThinkingBlock = false,
}: Props) {
  const [copyLabel, setCopyLabel] = useState<"Copy" | "Copied ✓">("Copy");

  const thinkEntries = thinkingLog ?? streamingThinkingEntries ?? [];
  const steps = toolEvents ?? [];

  function handleCopy() {
    const parts = [
      ...steps.map((t) => t.tool + (t.done ? " ok" : " pending")),
      content,
    ].join("\n");
    navigator.clipboard.writeText(parts).catch(() => {
      // Clipboard denied; fail silently.
    });
    setCopyLabel("Copied ✓");
    setTimeout(() => setCopyLabel("Copy"), 1200);
  }

  return (
    <div className="group relative flex flex-col items-stretch">
      <div className="flex-1 min-w-0 flex flex-col gap-2">
        {/* Thinking block */}
        {!hideThinkingBlock && (thinkEntries.length > 0 || !!streamingThinkingChunk || streaming) && (
          <ThinkingBlock
            entries={thinkEntries}
            activeChunk={streamingThinkingChunk}
            streaming={streaming}
          />
        )}

        {/* Tool steps, shown as activity lines */}
        {steps.length > 0 && (
          <div className="grid min-w-0 gap-1.5">{steps.map((event) => <ActivityLine key={event.id} event={event} />)}</div>
        )}
        {steps.length > 0 && <TeamLinks events={steps} />}

        {/* Content. Finished messages get the same markdown treatment as
            QAMessage (a turn with steps must not lose answer formatting);
            streaming stays plain text — markdown on partial chunks flickers. */}
        {progress ? (
          <ProgressLine text={content} />
        ) : breadcrumb ? (
          <BreadcrumbLine text={content} />
        ) : streaming ? (
          <div className="text-prose text-text leading-[1.65] whitespace-pre-wrap [overflow-wrap:anywhere]">
            {content}
            <span
              className="inline-block w-px h-3.5 bg-accent align-middle ml-px"
              style={{ animation: "blink 1s steps(2) infinite" }}
            />
          </div>
        ) : content !== "" ? (
          <MarkdownContent content={content} />
        ) : null}
      </div>

      {/* Copy button on hover — hidden while streaming */}
      {!streaming && (
        <button
          type="button"
          onClick={handleCopy}
          className={[
            "absolute top-0 right-0",
            "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 transition-opacity duration-150",
            "inline-flex items-center gap-1 px-1.5 py-0.5 rounded-control text-caption text-text-3 cursor-pointer",
            "bg-surface-2 border border-border-strong",
            "hover:text-text hover:border-[var(--accent-brd)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--focus-ring)]",
          ].join(" ")}
          aria-label="Copy message"
        >
          <Icon name="copy" size={10} />
          {copyLabel}
        </button>
      )}
    </div>
  );
}

// ── Breadcrumb line ────────────────────────────────────────────────────────────

/** Markers and their icon mappings. */
const MARKER_ICONS: Array<{ char: string; icon: "check" | "x" | "retry"; color: string }> = [
  { char: "✓", icon: "check", color: "text-green" },
  { char: "✗", icon: "x", color: "text-red" },
  { char: "↻", icon: "retry", color: "text-accent" },
  { char: "↩", icon: "retry", color: "text-accent" },
];

// ── Progress line ──────────────────────────────────────────────────────────────

function ProgressLine({ text }: { text: string }) {
  // Narration: the main thing to read between collapsed activity lines, so it
  // renders as answer prose (body size, normal color, markdown), not muted status text.
  return <MarkdownContent content={text} />;
}

function BreadcrumbLine({ text }: { text: string }) {
  // Check if the text starts with a known marker character.
  const match = MARKER_ICONS.find((m) => text.startsWith(m.char));
  const displayText = match ? text.slice(match.char.length).trimStart() : text;

  return (
    <div className="flex items-center gap-2 text-caption text-text-2">
      {match && (
        <Icon name={match.icon} size={11} className={match.color} />
      )}
      <span>{displayText}</span>
    </div>
  );
}
