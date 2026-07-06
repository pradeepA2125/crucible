interface Props {
  reason: "missing" | "malformed";
  message: string;
  building: boolean;
  onBuild: () => void;
}

export function EmptyState({ reason, message, building, onBuild }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
      <div className="text-[10px] uppercase tracking-[0.3em] text-[var(--color-text-dim)]">
        {reason === "missing" ? "No index snapshot" : "Snapshot unreadable"}
      </div>
      <p className="text-sm text-[var(--color-text-dim)] max-w-md">
        {reason === "missing"
          ? "The dependency space renders from .ai-editor/index-snapshot.json — build the index to ignite it."
          : message}
      </p>
      <button
        type="button"
        onClick={onBuild}
        disabled={building}
        className="px-4 py-2 rounded-lg text-xs font-semibold bg-[#fb923c] text-[#160709] disabled:opacity-50"
      >
        {building ? "Building…" : "Build index"}
      </button>
    </div>
  );
}
