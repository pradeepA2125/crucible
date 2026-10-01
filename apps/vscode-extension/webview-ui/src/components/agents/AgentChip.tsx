/** An agent's definition name as a chip; read-only `explore` gets the code tint. */
export function AgentChip({ name, label }: { name: string; label?: string }) {
  const readOnly = name === "explore";
  return (
    <span
      className="whitespace-nowrap rounded-full border px-1.5 text-[9.5px] leading-[15px]"
      style={readOnly
        ? { color: "var(--color-code)", borderColor: "rgba(125,211,252,.3)" }
        : { color: "var(--color-accent-ink)", borderColor: "var(--accent-brd)" }}
    >
      {label ? `${label} · ${name}` : name}
    </span>
  );
}
