import type { ReactNode } from "react";
import { Icon } from "../Icon";
import type { IconName } from "../Icon";

// ── BtnPrimary ────────────────────────────────────────────────────────────────

interface BtnPrimaryProps {
  onClick?: () => void;
  disabled?: boolean;
  /** When true, applies flex-1 so the button fills its flex container. */
  flex?: boolean;
  icon?: IconName;
  className?: string;
  children: ReactNode;
}

/**
 * Flat accent button: --accent-fill and --on-accent text.
 * Used for the primary/affirmative action in every card.
 */
export function BtnPrimary({ onClick, disabled, flex, icon, className = "", children }: BtnPrimaryProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        "inline-flex items-center justify-center gap-1.5 px-3 py-[6px]",
        "rounded-md text-[11px] font-[550] cursor-pointer",
        "border border-transparent bg-[var(--accent-fill)] hover:bg-[var(--accent-fill-hover)]",
        "disabled:opacity-50 disabled:cursor-default",
        flex ? "flex-1" : "",
        className,
      ].filter(Boolean).join(" ")}
      style={{
        color: "var(--on-accent)",
      }}
    >
      {icon && <Icon name={icon} size={11} />}
      {children}
    </button>
  );
}

// ── BtnGhost ──────────────────────────────────────────────────────────────────

interface BtnGhostProps {
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
}

/**
 * Ghost button — transparent bg, border-strong border, text-2 color.
 * Used for secondary actions (e.g. "Give feedback", "Allow & remember").
 */
export function BtnGhost({ onClick, disabled, className = "", children }: BtnGhostProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        "inline-flex items-center justify-center px-3 py-[6px]",
        "rounded-md text-[11px] font-[550] text-text-2 cursor-pointer",
        "bg-transparent border border-border-strong",
        "hover:bg-surface-2 hover:text-text",
        "transition-colors duration-150",
        "disabled:opacity-50 disabled:cursor-default",
        className,
      ].filter(Boolean).join(" ")}
    >
      {children}
    </button>
  );
}

// ── BtnDanger ─────────────────────────────────────────────────────────────────

interface BtnDangerProps {
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
}

/**
 * Danger button — red text, red-brd border, red-bg on hover.
 * Used for destructive/rejection actions.
 */
export function BtnDanger({ onClick, disabled, className = "", children }: BtnDangerProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        "inline-flex items-center justify-center px-3 py-[6px]",
        "rounded-md text-[11px] font-[550] cursor-pointer",
        "bg-transparent border transition-colors duration-150",
        "disabled:opacity-50 disabled:cursor-default",
        className,
      ].filter(Boolean).join(" ")}
      style={{ color: "var(--color-red)", borderColor: "var(--red-brd)" }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "var(--red-bg)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "transparent";
      }}
    >
      {children}
    </button>
  );
}
