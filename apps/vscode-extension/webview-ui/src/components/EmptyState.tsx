import { Icon } from "./Icon";
import type { IconName } from "./Icon";

interface Props {
  /** Called with the chip text when a chip is clicked. Pre-fills the input; does NOT send. */
  onPick: (text: string) => void;
}

interface Chip {
  icon: IconName;
  text: string;
}

const CHIPS: Chip[] = [
  { icon: "bolt", text: "Add error handling to the API routes" },
  { icon: "search", text: "Where is the planning loop defined?" },
  { icon: "bug", text: "Fix the TypeScript errors in editor-client" },
];

/**
 * EmptyState — shown in the thread view before any messages exist.
 *
 * Centered column with an animated spark tile, headline, sub-text, and three
 * prompt chips. Clicking a chip pre-fills the input (does NOT send).
 */
export function EmptyState({ onPick }: Props) {
  return (
    <div className="flex flex-col items-center justify-center gap-3.5 px-6 text-center flex-1">
      {/* Animated spark tile */}
      <div
        className="flex items-center justify-center rounded-[13px]"
        style={{
          width: 44,
          height: 44,
          background:
            "linear-gradient(180deg, var(--color-accent-deep), var(--color-accent-hot))",
          border: "1px solid var(--accent-brd)",
          color: "var(--color-accent-ink)",
          animation: "breathe 3.2s ease-in-out infinite",
        }}
      >
        <Icon name="spark" size={20} />
      </div>

      {/* Headline */}
      <h2
        className="font-semibold text-text m-0"
        style={{ fontSize: "14.5px" }}
      >
        What are we building?
      </h2>

      {/* Sub-text */}
      <p className="text-text-3 text-xs m-0">
        Describe a change, ask a question, or explore the codebase.
      </p>

      {/* Prompt chips */}
      <div className="flex flex-col gap-2 w-full max-w-xs">
        {CHIPS.map((chip) => (
          <button
            key={chip.text}
            type="button"
            onClick={() => onPick(chip.text)}
            className={[
              "flex items-center gap-2 w-full px-3 py-2 rounded-lg",
              "text-left text-xs text-text-2",
              "border transition-all duration-150",
              "hover:-translate-y-px",
            ].join(" ")}
            style={{
              background: "var(--color-surface)",
              borderColor: "var(--color-border)",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "var(--accent-brd)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "var(--color-border)";
            }}
          >
            <span style={{ color: "var(--color-accent)", flexShrink: 0 }}>
              <Icon name={chip.icon} size={12} />
            </span>
            <span className="flex-1 text-left">{chip.text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
