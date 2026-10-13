interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: string;
}

/**
 * Switch — animated toggle replacing raw checkboxes in Settings.
 * Track fills with the accent when on; the knob slides
 * with the spring ease. Motion collapses under prefers-reduced-motion.
 */
export function Switch({ checked, onChange, disabled, label }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative inline-flex w-[26px] h-[15px] flex-shrink-0 rounded-full border cursor-pointer disabled:opacity-50 disabled:cursor-default"
      style={{
        background: checked
          ? "var(--accent-fill)"
          : "var(--color-surface-3)",
        borderColor: checked ? "transparent" : "var(--color-border-strong)",
        transition:
          "background var(--dur-base) var(--ease-out)",
      }}
    >
      <span
        aria-hidden="true"
        className="absolute rounded-full"
        style={{
          width: 11,
          height: 11,
          top: 1,
          left: checked ? 12 : 1,
          transition: "left var(--dur-base) var(--ease-spring)",
          background: checked ? "var(--on-accent)" : "var(--text-2)",
        }}
      />
    </button>
  );
}
