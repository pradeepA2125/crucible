import { useEffect } from "react";
import { Icon, type IconName } from "./Icon";
import { SECTIONS, type SectionId } from "../settings/sections/meta";

// Overview isn't in the settings NavRail registry (it's the panel's landing hub),
// so prepend it here — the drawer offers it as a first-class destination too.
const DRAWER_SECTIONS: { id: SectionId; label: string; icon: IconName }[] = [
  { id: "overview", label: "Overview", icon: "home" },
  ...SECTIONS.map((s) => ({ id: s.id, label: s.label, icon: s.icon })),
];

interface Props {
  open: boolean;
  onClose: () => void;
  onSelect: (section: SectionId) => void;
}

/**
 * Collapsible left drawer in the chat window listing the Settings sections.
 * Selecting one deep-links the full Settings pane to that section (the host opens
 * the panel via the openSettings message). Slides in over the transcript with a
 * scrim; closes on scrim click, Escape, or re-toggling the header ☰.
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
    <div className="absolute inset-0 z-30 flex">
      {/* Panel */}
      <div
        className="anim-slide-in-left flex h-full w-56 flex-col"
        style={{ background: "var(--color-bg-2)", borderRight: "1px solid var(--color-border)" }}
      >
        <div
          className="flex items-center gap-2 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide flex-shrink-0"
          style={{ color: "var(--color-text-3)", borderBottom: "1px solid var(--color-border)" }}
        >
          <Icon name="gear" size={12} />
          Settings
        </div>
        <nav className="flex-1 overflow-y-auto py-1" aria-label="Settings sections">
          {DRAWER_SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => onSelect(s.id)}
              className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-xs transition-colors duration-150"
              style={{ color: "var(--color-text-2)", background: "transparent" }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background = "var(--accent-bg)";
                (e.currentTarget as HTMLButtonElement).style.color = "var(--color-accent)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background = "transparent";
                (e.currentTarget as HTMLButtonElement).style.color = "var(--color-text-2)";
              }}
            >
              <Icon name={s.icon} size={13} />
              <span className="truncate">{s.label}</span>
            </button>
          ))}
        </nav>
      </div>
      {/* Scrim — clicking outside the panel dismisses. */}
      <div
        data-testid="drawer-scrim"
        className="flex-1"
        onClick={onClose}
        style={{ background: "rgba(0,0,0,0.35)" }}
      />
    </div>
  );
}
