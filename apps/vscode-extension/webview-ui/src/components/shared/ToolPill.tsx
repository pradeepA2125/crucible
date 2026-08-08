import type { ToolEventView } from "../../types";
import { Icon } from "../Icon";
import { toolIcon } from "./tool-icon";

interface Props {
  event: ToolEventView;
  /** Id of the sibling panel this pill discloses; see `aria-controls` below. */
  panelId: string;
  expanded: boolean;
  onToggle: () => void;
}

/**
 * Single tool pill — a node on the serpentine track.
 *
 * Running: shimmer-bg gradient with spinner, not clickable.
 * Done ok: surface bg with green check.
 * Done error: red x with red border.
 *
 * Expansion state is owned by ToolTrack, which mounts the detail panel below
 * the row rather than inside this component.
 */
export function ToolPill({ event, panelId, expanded, onToggle }: Props) {
  const running = !event.done;
  const isError = event.isError === true;

  function handleClick() {
    if (!event.done) return;
    onToggle();
  }

  const pillBase =
    "inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full mono text-[11px] border transition-colors duration-150 flex-none";

  let pillStyle: React.CSSProperties = {};
  let pillClass = pillBase;

  if (running) {
    pillClass += " shimmer-bg border-[var(--accent-brd)] text-accent-ink cursor-default";
  } else if (expanded) {
    pillClass += " border-[var(--accent-brd)] text-accent-ink";
    pillStyle = { background: "var(--accent-bg)" };
  } else if (isError) {
    pillClass += " bg-surface border-[var(--red-brd)] text-text-3 cursor-pointer";
  } else {
    pillClass += " bg-surface border-border text-text-3 cursor-pointer hover:border-border-strong hover:text-text-2";
  }

  const icon = toolIcon(event.tool);

  return (
    <button
      type="button"
      data-event-id={event.id}
      className={pillClass}
      style={pillStyle}
      onClick={handleClick}
      aria-expanded={event.done ? expanded : undefined}
      aria-controls={event.done ? panelId : undefined}
    >
      <Icon name={icon} size={10} />
      <span>{event.tool}</span>

      {running && (
        <span
          className="w-[9px] h-[9px] rounded-full border-2 border-t-accent animate-spin"
          style={{ borderColor: "var(--accent-brd)", borderTopColor: "var(--color-accent)" }}
        />
      )}

      {!running && !isError && <Icon name="check" size={9} className="text-green" />}
      {!running && isError && <Icon name="x" size={9} className="text-red" />}

      {event.done && (
        <Icon
          name="chev-d"
          size={9}
          className={[
            "transition-transform duration-150",
            expanded ? "rotate-180" : "",
          ].join(" ")}
        />
      )}
    </button>
  );
}
