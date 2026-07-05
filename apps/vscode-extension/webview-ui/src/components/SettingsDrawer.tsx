import { useEffect } from "react";
import { Icon, type IconName } from "./Icon";
import { SECTIONS, OVERVIEW_TINT, type SectionId } from "../settings/sections/meta";

// Overview isn't in the settings NavRail registry (it's the panel's landing hub),
// so prepend it here — the drawer offers it as a first-class destination too.
const DRAWER_SECTIONS: { id: SectionId; label: string; icon: IconName; tint: string }[] = [
  { id: "overview", label: "Overview", icon: "home", tint: OVERVIEW_TINT },
  ...SECTIONS.map((s) => ({ id: s.id, label: s.label, icon: s.icon, tint: s.tint })),
];

interface Props {
  open: boolean;
  onClose: () => void;
  onSelect: (section: SectionId) => void;
}

/**
 * Collapsible left drawer in the chat window listing the Settings sections.
 * Selecting one opens the settings overlay at that section. Rendered as an inline
 * flex sibling so it SQUEEZES the chat column (no overlay/scrim); closes on Escape
 * or by re-toggling the header ☰.
 */
export function SettingsDrawer({ open, onClose, onSelect }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="anim-slide-in-left flex h-full w-52 flex-shrink-0 flex-col"
      style={{ background: "var(--color-surface)", borderRight: "1px solid var(--color-border-strong)" }}
    >
      <div
        className="flex items-center gap-2 px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wide flex-shrink-0"
        style={{
          color: "var(--color-text-2)",
          borderBottom: "1px solid var(--color-border)",
          background:
            "linear-gradient(180deg, rgba(139,92,246,.10), rgba(139,92,246,0)), transparent",
        }}
      >
        <span style={{ color: "var(--color-accent)", display: "inline-flex" }}>
          <Icon name="gear" size={12} />
        </span>
        Settings
      </div>
      <nav className="flex-1 overflow-y-auto px-2 py-2" aria-label="Settings sections">
        {DRAWER_SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => onSelect(s.id)}
            className="menu-item mb-0.5 flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs"
            style={{ color: "var(--color-text-2)" }}
          >
            <span style={{ color: s.tint, display: "inline-flex" }}>
              <Icon name={s.icon} size={13} />
            </span>
            <span className="truncate">{s.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
