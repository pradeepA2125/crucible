import { useEffect } from "react";
import SettingsApp from "../settings/SettingsApp";
import type { SectionId } from "../settings/sections/meta";
import { Icon } from "./Icon";

interface Props {
  section: SectionId;
  onClose: () => void;
}

/**
 * Floating in-chat settings popup. Renders the full SettingsApp (with its own
 * NavRail) as a centered modal card over the chat, opened at `section`. Closes on
 * ✕, Escape, or a backdrop click. Replaces opening settings in a separate VS Code
 * editor tab — settings now live inside the chat webview.
 */
export function ChatSettingsOverlay({ section, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      data-testid="overlay-backdrop"
      role="presentation"
      className="scrim absolute inset-0 z-40 flex items-center justify-center p-4"
      // Close only when the click lands on the backdrop itself — clicks that bubble
      // up from the card interior have a different target, so the card stays open.
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="AI Editor settings"
        className="surface-card anim-pop flex h-[85%] max-h-[720px] w-full max-w-[720px] flex-col overflow-hidden"
      >
        {/* Titlebar — a faint violet accent wash for a touch of warmth. */}
        <div
          className="accent-wash flex flex-shrink-0 items-center justify-between px-3 py-2"
          style={{ borderBottom: "1px solid var(--color-border-strong)" }}
        >
          <div
            className="flex items-center gap-2 text-xs font-semibold"
            style={{ color: "var(--color-text)" }}
          >
            <span style={{ color: "var(--color-accent)", display: "inline-flex" }}>
              <Icon name="gear" size={13} />
            </span>
            Settings
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            title="Close"
            className="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md text-text-3 transition-colors duration-150 hover:bg-surface-2 hover:text-text"
          >
            <Icon name="x" size={13} />
          </button>
        </div>
        {/* Body — the full settings app, opened at the requested section. */}
        <div className="min-h-0 flex-1 overflow-hidden">
          <SettingsApp initialSection={section} />
        </div>
      </div>
    </div>
  );
}
