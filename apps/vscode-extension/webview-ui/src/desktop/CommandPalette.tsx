import { useEffect, useRef, useState } from "react";
import { useModalFocus } from "../components/shared/useModalFocus";
import type { WorkspacePort } from "./workspaceClient";
export interface PaletteItem { id: string; label: string; detail?: string; disabled?: boolean; run(): void | Promise<void>; }
export function CommandPalette({ items, files, openFile, close }: { items: PaletteItem[]; files?: WorkspacePort; openFile(path: string): void; close(): void }) {
  const root = useRef<HTMLDivElement>(null), [query, setQuery] = useState(""), [paths, setPaths] = useState<string[]>([]), [status, setStatus] = useState(""), [active, setActive] = useState(0);
  const generation = useRef(0); useModalFocus(root, close);
  useEffect(() => {
    const id = ++generation.current; setPaths([]); setStatus(""); setActive(0);
    if (!files || !query.trim()) return;
    const timer = setTimeout(() => { setStatus("Searching workspace paths…");
      void files.search(query.trim().slice(0,256), false).then(result => {
        if (generation.current !== id) return;
        setPaths(result.entries.filter(item => item.kind === "file").map(item => item.path).slice(0,40));
        setStatus(result.partial ? "Search is partial. Refine your search." : result.warnings.join(" "));
      }).catch(error => { if (generation.current === id) setStatus(error instanceof Error ? error.message : "File search unavailable."); });
    }, 180);
    return () => { clearTimeout(timer); ++generation.current; files.cancelSearch(); };
  }, [query, files]);
  const filtered: PaletteItem[] = [...items.filter(item => `${item.label} ${item.detail ?? ""}`.toLowerCase().includes(query.toLowerCase())).slice(0,40),
    ...paths.map(path => ({ id: `file:${path}`, label: path, detail: "Workspace file", run: () => openFile(path) }))];
  const choose = (item?: PaletteItem) => { if (!item || item.disabled) return; close(); void item.run(); };
  return <div className="desktop-palette-scrim" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <div ref={root} role="dialog" aria-modal="true" aria-label="Search conversations, files and actions" className="desktop-palette" onKeyDown={event => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setActive(value => Math.max(0, Math.min(filtered.length-1, value + (event.key === "ArrowDown" ? 1 : -1)))); }
      if (event.key === "Enter" && event.target instanceof HTMLInputElement) { event.preventDefault(); choose(filtered[active]); }
    }}><input aria-label="Search everything" role="combobox" aria-expanded="true" aria-controls="desktop-palette-results" aria-activedescendant={filtered[active] ? `palette-${active}` : undefined} placeholder="Search conversations, file paths and actions…" value={query} onChange={event => setQuery(event.target.value)} />
      <div id="desktop-palette-results" role="listbox">{filtered.map((item,index) => <button key={item.id} id={`palette-${index}`} role="option" aria-selected={active === index} disabled={item.disabled} ref={element => { if (active === index) element?.scrollIntoView?.({ block: "nearest" }); }} onMouseEnter={() => setActive(index)} onClick={() => choose(item)}><span>{item.label}</span><small>{item.detail}</small></button>)}</div>
      {(!filtered.length || status) && <p role="status">{status || "No matches."}</p>}<footer>↑ ↓ to choose · Enter to open · Esc to close</footer>
    </div>
  </div>;
}
