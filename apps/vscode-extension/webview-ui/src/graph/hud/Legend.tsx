export function Legend() {
  const rows: [string, string][] = [
    ["var(--ax-star)", "file \u2014 mass = coupling"],
    ["var(--ax-beacon)", "entry point beacon"],
    ["var(--ax-k-calls)", "energy = dependencies"],
  ];
  return (
    <div className="ax-glass absolute top-4 right-4 px-4 py-3 text-[11px] text-[var(--ax-ink)]">
      <div className="text-[9px] uppercase tracking-[0.2em] opacity-50 mb-2">Reading the space</div>
      {rows.map(([c, label]) => (
        <div key={label} className="flex items-center gap-2 mt-1 opacity-80">
          <span className="w-2 h-2 rounded-full" style={{ background: c, boxShadow: `0 0 6px ${c}` }} />
          {label}
        </div>
      ))}
    </div>
  );
}
