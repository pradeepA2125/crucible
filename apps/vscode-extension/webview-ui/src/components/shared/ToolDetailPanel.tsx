import type { ToolEventView } from "../../types";
import { Icon } from "../Icon";
import { toolIcon } from "./tool-icon";

interface Props {
  event: ToolEventView;
}

/**
 * Expanded input/output panel for one tool call.
 *
 * Mounted by ToolTrack below the pill's row rather than inside the pill's own
 * column: with rows packed at a fixed width, a ~230px panel under a single chip
 * would blow out its column.
 */
export function ToolDetailPanel({ event }: Props) {
  const icon = toolIcon(event.tool);
  const isError = event.isError === true;
  const outputLineCount = event.output ? event.output.split("\n").length : 0;

  return (
    <div
      className="anim-rise rounded-lg border overflow-hidden"
      style={{
        borderColor: "var(--accent-brd)",
        background: "var(--color-surface)",
        boxShadow: "inset 0 1px 0 var(--hairline), 0 8px 20px -10px rgba(0,0,0,.5)",
      }}
    >
      <div
        className="flex items-center gap-1.5 px-[11px] py-[7px] border-b border-border"
        style={{ background: "linear-gradient(180deg, var(--accent-bg), transparent)" }}
      >
        <Icon name={icon} size={11} className="text-accent" />
        <span className="mono text-[11px] font-semibold text-accent-ink">{event.tool}</span>

        {event.thought && (
          <span className="text-[11px] italic text-text-3 ml-1">{event.thought}</span>
        )}

        <span className="ml-auto flex items-center gap-2 text-[9.5px] text-text-4">
          {outputLineCount > 0 && (
            <span
              className="text-[9px] font-semibold text-green px-1.5 py-px rounded-full border"
              style={{ background: "var(--green-bg)", borderColor: "var(--green-brd)" }}
            >
              {outputLineCount} lines
            </span>
          )}
          <span>collapse</span>
        </span>
      </div>

      <div className="px-[11px] py-2 border-b border-border">
        <div className="text-[9px] font-semibold uppercase tracking-widest text-text-4 mb-1.5">
          INPUT
        </div>
        <div className="mono text-[10.5px] leading-[1.8]">
          {Object.entries(event.args).map(([k, v]) => (
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
        <div className="text-[9px] font-semibold uppercase tracking-widest text-text-4 mb-1.5">
          OUTPUT
        </div>
        <pre
          className={[
            "mono text-[10.5px] leading-[1.75] max-h-24 overflow-y-auto whitespace-pre-wrap break-all",
            isError ? "text-red" : "text-text-2",
          ].join(" ")}
        >
          {event.output ?? ""}
        </pre>
      </div>
    </div>
  );
}
