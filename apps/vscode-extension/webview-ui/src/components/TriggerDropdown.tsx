export interface DropdownItem {
  id: string;
  label: string;
  sublabel?: string;
  badge?: string;
}

interface Props {
  items: DropdownItem[];
  activeIndex: number;
  onHover: (index: number) => void;
  onSelect: (id: string) => void;
}

/** Live "/" or "@" trigger dropdown — positioned above the composer like ModelMenu. */
export function TriggerDropdown({ items, activeIndex, onHover, onSelect }: Props) {
  if (items.length === 0) return null;

  return (
    <div
      className="anim-rise absolute bottom-full left-0 z-50 mb-1.5 max-h-56 w-[280px] overflow-y-auto rounded-[10px] border p-1"
      style={{ background: "var(--color-surface)", borderColor: "var(--color-border-strong)" }}
      role="listbox"
    >
      {items.map((item, i) => (
        <div
          key={item.id}
          data-testid={`trigger-item-${item.id}`}
          data-active={i === activeIndex ? "true" : "false"}
          role="option"
          aria-selected={i === activeIndex}
          onMouseEnter={() => onHover(i)}
          onClick={() => onSelect(item.id)}
          className="menu-item flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs"
          style={{
            color: "var(--color-text-2)",
            background: i === activeIndex ? "var(--accent-bg)" : "transparent",
          }}
        >
          <span className="min-w-0 flex-1 truncate" style={{ color: "var(--color-text)" }}>{item.label}</span>
          {item.sublabel && (
            <span className="min-w-0 flex-1 truncate text-[10.5px]" style={{ color: "var(--color-text-3)" }}>
              {item.sublabel}
            </span>
          )}
          {item.badge && (
            <span
              className="shrink-0 rounded px-1.5 py-px text-[9.5px] font-semibold"
              style={{ background: "var(--color-surface-3)", color: "var(--color-text-2)" }}
            >
              {item.badge}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
