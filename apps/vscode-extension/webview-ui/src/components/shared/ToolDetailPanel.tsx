import type { ToolEventView } from "../../types";
import { Icon } from "../Icon";
import { toolIcon } from "./tool-icon";

interface Props {
  event: ToolEventView;
}

/**
 * Expanded input/output panel for one tool call.
 *
 * Mounted under its ActivityLine.
 */
export function ToolDetailPanel({ event }: Props) {
  const icon = toolIcon(event.tool);
  const isError = event.isError === true;
  const outputLineCount = event.output ? event.output.split("\n").length : 0;

  return (
    <div
      className="min-w-0 anim-rise rounded-card border overflow-hidden"
      style={{
        borderColor: "var(--border)",
        background: "var(--color-surface)",
      }}
    >
      <div
        className="flex min-w-0 flex-wrap items-center gap-1.5 px-[11px] py-[7px] border-b border-border"
        style={{ background: "var(--surface-2)" }}
      >
        <Icon name={icon} size={11} className="text-accent" />
        <span className="min-w-0 [overflow-wrap:anywhere] mono text-caption font-semibold text-accent-ink">{event.tool}</span>

        {event.thought && (
          <span className="tool-thought w-full min-w-0 [overflow-wrap:anywhere] text-caption text-text-2">{event.thought}</span>
        )}

        <span className="ml-auto flex items-center gap-2 text-caption text-text-4">
          {outputLineCount > 0 && (
            <span
              className="text-caption font-semibold text-green px-1.5 py-px rounded-full border"
              style={{ background: "var(--green-bg)", borderColor: "var(--green-brd)" }}
            >
              {outputLineCount} lines
            </span>
          )}
        </span>
      </div>

      <div className="px-[11px] py-2 border-b border-border">
        <div className="text-caption font-semibold uppercase tracking-widest text-text-4 mb-1.5">
          INPUT
        </div>
        <div className="min-w-0 [overflow-wrap:anywhere] mono text-caption leading-[1.8]">
          {Object.entries(event.args ?? {}).map(([k, v]) => (
            <div key={k}>
              <span className="text-text-3">{k}:</span>{" "}
              <span className="text-accent-ink">
                {typeof v === "string" ? v : JSON.stringify(v)}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="px-[11px] py-2">
        <div className="text-caption font-semibold uppercase tracking-widest text-text-4 mb-1.5">
          OUTPUT
        </div>
        <pre
          className={[
            "mono text-caption leading-[1.75] max-h-24 overflow-y-auto whitespace-pre-wrap break-all",
            isError ? "text-red" : "text-text-2",
          ].join(" ")}
        >
          {event.output ?? ""}
        </pre>
      </div>
    </div>
  );
}
