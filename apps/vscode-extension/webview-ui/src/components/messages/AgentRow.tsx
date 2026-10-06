import { useState } from "react";
import type { ToolEventView } from "../../types";
import { Avatar } from "../shared/Avatar";
import { MarkdownContent } from "../shared/MarkdownContent";
import { ThinkingBlock } from "../shared/ThinkingBlock";
import { ToolTrack } from "../shared/ToolTrack";
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
}

/**
 * Generic agent row — handles breadcrumbs, tool pills, streaming content.
 * Matches .turn / .crumb / .stream-line / .caret in the hi-fi mockup.
 *
 * breadcrumb: compact icon+text row, strips leading marker character.
 * progress: compact icon+text row for a non-terminal mid-turn status note —
 *   breadcrumb-adjacent but never strips a marker (a progress note has none).
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
}: Props) {
  const [copyLabel, setCopyLabel] = useState<"Copy" | "Copied ✓">("Copy");

  const thinkEntries = thinkingLog ?? streamingThinkingEntries ?? [];
  const pills = toolEvents ?? [];

  function handleCopy() {
    const parts = [
      ...pills.map((t) => t.tool + (t.done ? " ok" : " pending")),
      content,
    ].join("\n");
    navigator.clipboard.writeText(parts).catch(() => {
      // Clipboard denied; fail silently.
    });
    setCopyLabel("Copied ✓");
    setTimeout(() => setCopyLabel("Copy"), 1200);
  }

  return (
    <div className="group relative flex gap-2.5 items-start">
      <Avatar />

      <div className="flex-1 min-w-0 flex flex-col gap-2">
        {/* Thinking block */}
        {(thinkEntries.length > 0 || !!streamingThinkingChunk || streaming) && (
          <ThinkingBlock
            entries={thinkEntries}
            activeChunk={streamingThinkingChunk}
            streaming={streaming}
          />
        )}

        {/* Tool pills, threaded onto a serpentine track */}
        {pills.length > 0 && <ToolTrack events={pills} />}
        {pills.length > 0 && <TeamLinks events={pills} />}

        {/* Content. Finished messages get the same markdown treatment as
            QAMessage (a turn with pills must not lose answer formatting);
            streaming stays plain text — markdown on partial chunks flickers. */}
        {progress ? (
          <ProgressLine text={content} />
        ) : breadcrumb ? (
          <BreadcrumbLine text={content} />
        ) : streaming ? (
          <div className="text-xs text-text-3 whitespace-pre-wrap">
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
  // De-emphasized, breadcrumb-adjacent but distinct: muted italic, no marker
  // stripping (a progress note is plain narration, never a "✓ done"-style
  // marker string). `clock` reads as "in progress" without implying success/failure.
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <Icon name="clock" size={11} className="text-text-3" />
      <span className="text-text-3 italic">{text}</span>
    </div>
  );
}

function BreadcrumbLine({ text }: { text: string }) {
  // Check if the text starts with a known marker character.
  const match = MARKER_ICONS.find((m) => text.startsWith(m.char));
  const displayText = match ? text.slice(match.char.length).trimStart() : text;

  return (
    <div className="flex items-center gap-2 text-[11px] text-text-2">
      {match && (
        <Icon name={match.icon} size={11} className={match.color} />
      )}
      <span>{displayText}</span>
    </div>
  );
}
