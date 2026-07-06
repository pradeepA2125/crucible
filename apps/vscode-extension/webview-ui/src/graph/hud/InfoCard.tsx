import type { FileDetail, StarRecord } from "../types";

interface Props {
  star: StarRecord;
  detail: FileDetail | null;
  onOpen: () => void;
  onDive: () => void;
}

export function InfoCard({ star, detail, onOpen, onDive }: Props) {
  const name = star.id.slice(star.id.lastIndexOf("/") + 1);
  return (
    <div
      className="absolute bottom-5 left-4 w-80 px-4 py-4 rounded-xl
                 bg-[rgba(22,7,9,0.65)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md"
    >
      <div className="text-[10px] font-mono opacity-45 break-all">{star.id}</div>
      <div className="text-[15px] font-bold mt-1 text-[#fff4ea]">{name}</div>
      <div className="text-[9px] uppercase tracking-[0.2em] text-[#fbbf24] mt-0.5">
        {star.isEntry ? "entry point · " : star.isHub ? "hub · " : ""}file
      </div>
      <div className="flex gap-5 my-3">
        {(
          [
            [star.outDeg, "outgoing"],
            [star.inDeg, "incoming"],
            [star.symbolCount, "symbols"],
            [detail?.withinFileCount ?? "…", "within-file"],
          ] as const
        ).map(([v, l]) => (
          <div key={l}>
            <div className="text-[16px] font-bold text-[#fff4ea]">{v}</div>
            <div className="text-[8.5px] uppercase tracking-[0.15em] opacity-45">{l}</div>
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onOpen}
          className="flex-1 py-2 rounded-lg text-[11px] font-semibold bg-[#fb923c] text-[#160709]"
        >
          Open in editor
        </button>
        <button
          type="button"
          onClick={onDive}
          className="flex-1 py-2 rounded-lg text-[11px] font-semibold border border-[rgba(251,146,60,0.3)] text-[#fff4ea]"
        >
          Dive inside
        </button>
      </div>
    </div>
  );
}
