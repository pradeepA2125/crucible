import type { ReactNode } from "react";
import { Icon } from "../Icon";
import type { IconName } from "../Icon";

interface Props {
  icon: IconName;
  iconColor?: string;
  title: string;
  titleColor?: string;
  subtitle?: string;
  badge?: ReactNode;
  /** Extra header content (stats, trailing affordances, etc.). */
  trailing?: ReactNode;
  expandable?: boolean;
  expanded?: boolean;
  onToggle?: () => void;
  /** CSS color for the card border (overrides default). */
  borderColor?: string;
  /** CSS background for the header area (gate accent tint). */
  headerTint?: string;
  children?: ReactNode;
}

/**
 * CardShell — shared structural primitive for all card-style messages and gates.
 *
 * Produces: rounded card, inset hairline + soft drop-shadow, an icon+title header
 * (clickable toggle when expandable=true), and a children slot for the body.
 * All styling is driven by CSS vars so callers can override border/header tint.
 */
export function CardShell({
  icon,
  iconColor,
  title,
  titleColor,
  subtitle,
  badge,
  trailing,
  expandable = false,
  expanded = false,
  onToggle,
  borderColor,
  headerTint,
  children,
}: Props) {
  const headerContent = (
    <>
      {/* Icon */}
      <span className="flex-shrink-0" style={{ color: iconColor ?? "var(--color-accent)" }}>
        <Icon name={icon} size={13} />
      </span>

      {/* Title */}
      <span
        className="text-xs font-semibold"
        style={{ color: titleColor ?? "var(--color-text)" }}
      >
        {title}
      </span>

      {/* Subtitle — takes available space, truncated */}
      {subtitle && (
        <span className="text-[11px] text-text-3 flex-1 min-w-0 truncate">{subtitle}</span>
      )}
      {!subtitle && <span className="flex-1" />}

      {/* Optional badge */}
      {badge}

      {/* Optional trailing content (stats etc.) */}
      {trailing}

      {/* Chevron when expandable */}
      {expandable && (
        <span
          className="text-text-4 flex-shrink-0 transition-transform duration-[180ms]"
          style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}
        >
          <Icon name="chev-d" size={12} />
        </span>
      )}
    </>
  );

  return (
    <div
      className={[
        "rounded-[10px] border overflow-hidden bg-surface",
        "shadow-[inset_0_1px_0_var(--hairline),0_10px_24px_-14px_rgba(0,0,0,.55)]",
      ].join(" ")}
      style={{
        borderColor: borderColor ?? "var(--color-border)",
        transition: "border-color 0.2s",
      }}
    >
      {/* ── Header ── */}
      {expandable ? (
        <button
          type="button"
          onClick={onToggle}
          className="w-full flex items-center gap-2 px-3 py-[9px] cursor-pointer select-none bg-transparent border-0 text-left"
          style={headerTint ? { background: headerTint } : undefined}
        >
          {headerContent}
        </button>
      ) : (
        <div
          className="flex items-center gap-2 px-3 py-[9px] select-none"
          style={headerTint ? { background: headerTint } : undefined}
        >
          {headerContent}
        </div>
      )}

      {/* ── Body ── */}
      {children}
    </div>
  );
}
