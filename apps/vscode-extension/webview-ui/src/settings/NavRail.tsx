import { Icon } from "../components/Icon";
import type { IconName } from "../components/Icon";
import { SECTIONS, OVERVIEW_TINT, type SectionId } from "./sections/meta";

interface NavRailProps {
  active: SectionId;
  counts: Partial<Record<SectionId, number>>;
  onSelect: (id: SectionId) => void;
}

const ITEM_H = 30;
const ITEM_GAP = 2;
const PAD_TOP = 8; // p-2

/**
 * NavRail — Copilot-style left navigation. A single glowing 2px indicator
 * bar slides (spring ease) to the active item instead of re-rendering per row.
 */
export function NavRail({ active, counts, onSelect }: NavRailProps) {
  const items: { id: SectionId; label: string; icon: IconName; tint: string }[] = [
    { id: "overview", label: "Overview", icon: "home", tint: OVERVIEW_TINT },
    ...SECTIONS.map((s) => ({ id: s.id as SectionId, label: s.label, icon: s.icon, tint: s.tint })),
  ];
  const activeIdx = Math.max(0, items.findIndex((i) => i.id === active));

  return (
    <nav
      aria-label="Settings sections"
      className="relative flex w-[168px] flex-shrink-0 flex-col p-2"
      style={{ borderRight: "1px solid var(--color-border)" }}
    >
      {/* Sliding active indicator */}
      <span
        aria-hidden="true"
        className="absolute w-[2px] rounded-full"
        style={{
          left: 3,
          top: PAD_TOP + activeIdx * (ITEM_H + ITEM_GAP) + 7,
          height: 16,
          background: "var(--color-accent)",
          boxShadow: "0 0 8px var(--accent-glow)",
          transition: "top var(--dur-base) var(--ease-spring)",
        }}
      />
      {items.map((item) => {
        const isActive = item.id === active;
        return (
          <button
            key={item.id}
            type="button"
            aria-current={isActive ? "page" : undefined}
            onClick={() => onSelect(item.id)}
            className={[
              "menu-item flex items-center gap-2 h-[30px] mb-[2px] px-2.5 rounded-lg",
              "text-xs text-left cursor-pointer bg-transparent",
              isActive ? "" : "text-text-2",
            ].join(" ")}
            style={isActive ? { background: "var(--accent-bg)", color: "var(--color-accent-ink)" } : undefined}
          >
            <span style={{ color: item.tint, display: "inline-flex" }}>
              <Icon name={item.icon} size={12} />
            </span>
            <span className="flex-1 truncate">{item.label}</span>
            {counts[item.id] !== undefined && (
              <span
                className="rounded px-1 font-mono"
                style={{ fontSize: "9.5px", background: "var(--color-surface-3)", color: "var(--color-text-3)" }}
              >
                {counts[item.id]}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
