import type { EdgeKind, FileDetail, StarRecord, SymbolDetail } from "../types";

const KIND_VAR: Record<EdgeKind, string> = {
  Imports: "var(--ax-k-imports)",
  Calls: "var(--ax-k-calls)",
  Inherits: "var(--ax-k-inherits)",
  References: "var(--ax-k-references)",
};

interface Props {
  star: StarRecord;
  detail: FileDetail | null;
  /** When present (a satellite is picked at L3), the list shows the symbol's edges instead. */
  symbolDetail?: SymbolDetail | null;
  onOpen: () => void;
  onDive: () => void;
  /** Follow a connection: ride/fly to that file and refocus. */
  onGoEdge: (fileId: string) => void;
}

interface Row {
  key: string;
  dir: "out" | "in";
  kind: EdgeKind;
  fileId: string | null;
  label: string;
  sub: string | null;
  count: number;
  crossPackage: boolean;
}

function shortName(fileId: string): string {
  return fileId.slice(fileId.lastIndexOf("/") + 1);
}

function fileRows(detail: FileDetail): Row[] {
  const byKey = new Map<string, Row>();
  for (const e of detail.edges) {
    const key = `${e.dir} ${e.kind} ${e.otherFile}`;
    const row = byKey.get(key);
    if (row) {
      row.count += 1;
      continue;
    }
    byKey.set(key, {
      key,
      dir: e.dir,
      kind: e.kind,
      fileId: e.otherFile,
      label: shortName(e.otherFile),
      sub: e.symbolName ? `${e.symbolName}${e.line ? `:${e.line}` : ""}` : null,
      count: 1,
      crossPackage: e.crossPackage,
    });
  }
  return [...byKey.values()].sort(
    (a, b) => (a.dir === b.dir ? b.count - a.count : a.dir === "out" ? -1 : 1)
  );
}

function symbolRows(detail: SymbolDetail): Row[] {
  return detail.edges.map((e, i) => ({
    key: `${e.dir} ${e.kind} ${e.name} ${i}`,
    dir: e.dir,
    kind: e.kind,
    fileId: e.fileId,
    label: e.name,
    sub: e.fileId ? `${shortName(e.fileId)}${e.line ? `:${e.line}` : ""}` : null,
    count: 1,
    crossPackage: false,
  }));
}

function ConnectionRow({ row, onGo }: { row: Row; onGo: (fileId: string) => void }) {
  const inner = (
    <>
      <span style={{ color: row.dir === "out" ? "var(--ax-out)" : "var(--ax-in)" }}>
        {row.dir === "out" ? "→" : "←"}
      </span>
      <span
        className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
        style={{ background: KIND_VAR[row.kind] }}
        title={row.kind}
      />
      <span className="truncate text-[var(--ax-ink)]">{row.label}</span>
      {row.sub && <span className="opacity-45 truncate">{row.sub}</span>}
      {row.count > 1 && <span className="opacity-45">×{row.count}</span>}
      {row.crossPackage && (
        <span className="text-[8px] uppercase tracking-widest opacity-50 text-[var(--ax-beacon)]">pkg</span>
      )}
    </>
  );
  if (!row.fileId) {
    return <div className="flex items-center gap-1.5 w-full px-1.5 py-1 text-[10.5px] font-mono opacity-60">{inner}</div>;
  }
  const fileId = row.fileId;
  return (
    <button
      type="button"
      aria-label={`go to ${fileId}`}
      onClick={() => onGo(fileId)}
      className="ax-row-btn flex items-center gap-1.5 w-full px-1.5 py-1 rounded text-[10.5px] font-mono text-left"
    >
      {inner}
    </button>
  );
}

export function InfoCard({ star, detail, symbolDetail, onOpen, onDive, onGoEdge }: Props) {
  const name = star.id.slice(star.id.lastIndexOf("/") + 1);
  const rows = symbolDetail ? symbolRows(symbolDetail) : detail ? fileRows(detail) : [];
  return (
    <div
      className="ax-glass absolute bottom-5 left-4 w-80 px-4 py-4"
    >
      <div className="text-[10px] font-mono opacity-45 break-all">{star.id}</div>
      <div className="text-[15px] font-bold mt-1 text-[var(--ax-ink)]">{name}</div>
      <div className="text-[9px] uppercase tracking-[0.2em] text-[var(--ax-beacon)] mt-0.5">
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
            <div className="text-[16px] font-bold text-[var(--ax-ink)]">{v}</div>
            <div className="text-[8.5px] uppercase tracking-[0.15em] opacity-45">{l}</div>
          </div>
        ))}
      </div>
      {rows.length > 0 && (
        <div className="mb-3">
          <div className="text-[8.5px] uppercase tracking-[0.25em] opacity-45 mb-1">
            {symbolDetail ? "symbol connections" : "connections"}
          </div>
          <div className="max-h-44 overflow-y-auto pr-1">
            {rows.map((r) => (
              <ConnectionRow key={r.key} row={r} onGo={onGoEdge} />
            ))}
          </div>
        </div>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onOpen}
          className="flex-1 py-2 rounded-lg text-[11px] font-semibold bg-[var(--ax-accent)] text-[var(--ax-accent-text)]"
        >
          Open in editor
        </button>
        <button
          type="button"
          onClick={onDive}
          className="flex-1 py-2 rounded-lg text-[11px] font-semibold border border-[var(--ax-border)] text-[var(--ax-ink)]"
        >
          Dive inside
        </button>
      </div>
    </div>
  );
}
