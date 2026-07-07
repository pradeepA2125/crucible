import { useEffect, useMemo, useRef, useState } from "react";
import type { StarRecord, SymbolHit } from "../types";

interface Props {
  stars: StarRecord[];
  symbolHits: SymbolHit[];
  onQuerySymbols: (q: string) => void;
  onGoFile: (fileId: string) => void;
  onGoSymbol: (hit: SymbolHit) => void;
}

export function SearchBar({ stars, symbolHits, onQuerySymbols, onGoFile, onGoSymbol }: Props) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const fileHits = useMemo(() => {
    const lq = q.trim().toLowerCase();
    if (!lq) return [];
    return stars.filter((s) => s.id.toLowerCase().includes(lq)).slice(0, 5);
  }, [q, stars]);

  const symHits = q.trim() ? symbolHits.slice(0, 5) : [];
  const total = fileHits.length + symHits.length;

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const lq = q.trim();
    if (!lq) return;
    debounceRef.current = setTimeout(() => onQuerySymbols(lq), 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [q, onQuerySymbols]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function go(i: number): void {
    if (i < fileHits.length) {
      onGoFile(fileHits[i]!.id);
    } else if (symHits[i - fileHits.length]) {
      onGoSymbol(symHits[i - fileHits.length]!);
    }
    setQ("");
    setSel(0);
  }

  return (
    <div className="absolute bottom-5 left-1/2 -translate-x-1/2 w-[420px]">
      {total > 0 && (
        <div className="ax-glass mb-1.5 overflow-hidden">
          {fileHits.map((s, i) => (
            <button
              key={s.id}
              type="button"
              onClick={() => go(i)}
              className={`flex justify-between w-full px-3 py-2 text-[11px] font-mono text-left
                          ${i === sel ? "bg-[color-mix(in_srgb,var(--ax-accent)_16%,transparent)]" : ""}`}
            >
              <span className="text-[var(--ax-ink)]">{s.id}</span>
              <span className="text-[9px] uppercase tracking-widest opacity-40">file</span>
            </button>
          ))}
          {symHits.map((hit, j) => (
            <button
              key={hit.symbolId}
              type="button"
              onClick={() => go(fileHits.length + j)}
              className={`flex justify-between w-full px-3 py-2 text-[11px] font-mono text-left
                          ${fileHits.length + j === sel ? "bg-[color-mix(in_srgb,var(--ax-accent)_16%,transparent)]" : ""}`}
            >
              <span className="text-[var(--ax-ink)]">{hit.name}</span>
              <span className="text-[9px] uppercase tracking-widest opacity-40">{hit.kind}</span>
            </button>
          ))}
        </div>
      )}
      <input
        ref={inputRef}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setSel(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            setSel((s) => Math.min(total - 1, s + 1));
            e.preventDefault();
          }
          if (e.key === "ArrowUp") {
            setSel((s) => Math.max(0, s - 1));
            e.preventDefault();
          }
          if (e.key === "Enter" && total) go(sel);
          if (e.key === "Escape") setQ("");
        }}
        placeholder="search files & symbols — fly to anything (⌘K)"
        className="ax-glass w-full px-4 py-3 text-[12px] font-mono outline-none
                   text-[var(--ax-ink)] placeholder:opacity-35 focus:border-[var(--ax-accent)]"
      />
    </div>
  );
}
