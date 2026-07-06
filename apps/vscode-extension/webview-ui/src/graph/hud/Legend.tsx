import { EMBER } from "../palette";

export function Legend() {
  const rows: [string, string][] = [
    [EMBER.star, "file — mass = coupling"],
    [EMBER.beacon, "entry point beacon"],
    [EMBER.kinds.Calls, "energy = dependencies"],
  ];
  return (
    <div
      className="absolute top-4 right-4 px-4 py-3 rounded-xl text-[11px]
                 bg-[rgba(22,7,9,0.6)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md"
    >
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
