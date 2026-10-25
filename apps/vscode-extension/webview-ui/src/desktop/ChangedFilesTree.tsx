import { useEffect, useMemo, useState, type ReactNode, type CSSProperties } from "react";
import { Icon } from "../components/Icon";
import { fileIcon } from "./fileIcons";
export interface ReviewFile { path: string; status?: string; description?: string; counts?: { additions: number; deletions: number }; viewed?: boolean; earlier?: boolean; review?: ReactNode; }
export function ReviewCounts({ additions, deletions }: { additions: number; deletions: number }) {
  return <small className="desktop-diff-counts"><span className="is-add">+{additions.toLocaleString()}</span> <span className="is-del">−{deletions.toLocaleString()}</span></small>;
}
interface Node { name: string; path: string; file?: ReviewFile; children: Map<string, Node>; }
/** At most 500 leaves. Single-child folder chains compress without dropping path identity. */
export function ChangedFilesTree({ files, selected, select, disabled = false, onOrder }: { onOrder?(paths: string[]): void; files: ReviewFile[]; selected?: string; select(path: string): void; disabled?: boolean }) {
  const [query, setQuery] = useState(""), [closed, setClosed] = useState<Set<string>>(() => new Set()), [open, setOpen] = useState(true);
  const root = useMemo(() => {
    const tree = new Map<string, Node>();
    for (const file of files.slice(0, 500).filter(file => file.path.toLowerCase().includes(query.trim().toLowerCase()))) {
      let nodes = tree, prefix = ""; const parts = file.path.split("/"); if (file.path.length > 4096 || parts.length > 128) continue;
      parts.forEach((name, index) => {
        prefix = prefix ? `${prefix}/${name}` : name;
        if (!nodes.has(name)) nodes.set(name, { name, path: prefix, children: new Map() });
        const node = nodes.get(name)!; if (index === parts.length - 1) node.file = file; nodes = node.children;
      });
    }
    return tree;
  }, [files, query]);
  const order: string[] = [];
  const collect = (nodes: Map<string, Node>) => { for (const original of nodes.values()) { let node = original; while (!node.file && node.children.size === 1 && ![...node.children.values()][0]!.file) node = [...node.children.values()][0]!; if (node.file) order.push(node.path); if (node.children.size && (!!query || !closed.has(node.path))) collect(node.children); } };
  if (open) collect(root);
  const orderKey = JSON.stringify(order);
  useEffect(() => { onOrder?.(JSON.parse(orderKey) as string[]); }, [orderKey, onOrder]);
  const rows = (nodes: Map<string, Node>, depth = 0): ReactNode => <ul className={depth ? "review-tree-children" : undefined} style={depth ? { "--review-guide-left": `${14 + (depth - 1) * 16}px` } as CSSProperties : undefined}>{[...nodes.values()].map(original => {
    let node = original, name = node.name;
    while (!node.file && node.children.size === 1 && ![...node.children.values()][0]!.file) { node = [...node.children.values()][0]!; name += "/" + node.name; }
    const file = node.file, expanded = !!query || !closed.has(node.path);
    return <li key={node.path}>
      <div className="review-tree-row">{file ? <button type="button" className="desktop-file-row review-tree-file" aria-label={`${file.path}${file.status ? " " + file.status : ""}`} aria-describedby={file.description || file.viewed || file.earlier ? `review-path-${encodeURIComponent(file.path)}` : undefined} aria-current={selected === file.path ? "true" : undefined} title={file.path} disabled={disabled} style={{ paddingLeft: 8 + depth * 16 }} onClick={() => select(file.path)}>
        <Icon name={fileIcon(file.path)} size={14} /><span>{name}</span>{file.counts && <ReviewCounts {...file.counts} />}
        {file.status && <small className="review-status" data-status={file.status} aria-label={file.status}>{{ Added: "A", Modified: "M", Deleted: "D", Renamed: "R", Untracked: "U" }[file.status] ?? ""}</small>}
        {file.viewed && <Icon name="check" size={12} />}{file.earlier && <span className="review-recheck-dot" aria-label="Viewed earlier, open to confirm" />}
      </button> : <button type="button" className="desktop-file-row review-tree-folder" aria-label={node.path} aria-expanded={expanded} style={{ paddingLeft: 8 + depth * 16 }} onClick={() => setClosed(previous => { const next = new Set(previous); if (next.has(node.path)) next.delete(node.path); else next.add(node.path); return next; })}><Icon name={expanded ? "chev-d" : "chev-r"} size={12} /><span>{name}</span></button>}{file?.review}</div>
      {file && (file.description || file.viewed || file.earlier) && <span id={`review-path-${encodeURIComponent(file.path)}`} className="sr-only">{file.description}{file.viewed ? ". Viewed" : file.earlier ? ". Viewed earlier, open to confirm" : ""}</span>}
      {node.children.size > 0 && expanded && rows(node.children, depth + 1)}
    </li>;
  })}</ul>;
  return <aside aria-label="Changed files navigator" className="review-tree">
    <label className="review-filter"><Icon name="search" size={14} /><input aria-label="Filter changed files" placeholder="Filter files…" maxLength={256} value={query} onChange={event => setQuery(event.target.value)} /></label>
    <button type="button" className="desktop-file-section" aria-expanded={open} onClick={() => setOpen(!open)}><Icon name={open ? "chev-d" : "chev-r"} size={12} /><span>Changed files</span><small>{files.length}</small></button>
    {open && <div className="review-tree-scroll">{root.size ? rows(root) : <p className="desktop-empty">{query ? "No matching changed files." : "No changed files to list."}</p>}</div>}
    {files.length > 500 && <p role="status">Showing the first 500 changed files.</p>}
  </aside>;
}
