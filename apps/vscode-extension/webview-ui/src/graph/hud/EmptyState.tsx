interface Props {
  reason: "missing" | "malformed";
  message: string;
  building: boolean;
  onBuild: () => void;
}

export function EmptyState({ reason, message, building, onBuild }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
      <div className="text-[10px] uppercase tracking-[0.3em] text-[var(--ax-ink-dim)]">
        {reason === "missing" ? "No index snapshot" : "Snapshot unreadable"}
      </div>
      <p className="text-sm text-[var(--ax-ink-dim)] max-w-md">
        {reason === "missing"
          ? "The dependency space renders from .crucible/index-snapshot.json — build the index to ignite it."
          : message}
      </p>
      <button
        type="button"
        onClick={onBuild}
        disabled={building}
        className="px-4 py-2 rounded-lg text-xs font-semibold bg-[var(--ax-accent)] text-[var(--ax-accent-text)] disabled:opacity-50"
      >
        {building ? "Building…" : "Build index"}
      </button>
    </div>
  );
}
