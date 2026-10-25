import { Icon } from "../components/Icon";
import { useEffect, useState } from "react";
import { ThreadRow } from "../components/HistoryView";
import type { ThreadSummary } from "../types";
import type { Metadata, ThreadMetadataPort } from "./threadMetadataClient";
export function waitingLabel(kinds?: string[]): string {
  if (!kinds?.length) return "Needs attention";
  const names: Record<string, string> = { command: "Command approval", edit: "Edit approval", mcp_tool: "Tool approval", mode: "Plan decision", clarify: "Question", team_plan: "Team plan" };
  return [...new Set(kinds.map(kind => names[kind] ?? "Decision needed"))].join(" · ");
}
export function ThreadSidebar({ threads, active, project, query, locked, now, port, onSelect, onMetadata, ready, groupOpen = true, onGroupToggle }: {
  threads: ThreadSummary[]; active: string; project: string; query: string; locked: boolean; now: Date; port?: ThreadMetadataPort;
  groupOpen?: boolean; onGroupToggle?(): void;
  ready: boolean; onSelect(id: string): void; onMetadata?(value: Metadata): void;
}) {
  const [metadata, setMetadata] = useState<Metadata>({}), [loaded, setLoaded] = useState(!port);
  const [error, setError] = useState(""), [menu, setMenu] = useState<string>(), [editing, setEditing] = useState<string>(), [name, setName] = useState("");
  const [busy, setBusy] = useState(false), [showAll, setShowAll] = useState(false), [retry, setRetry] = useState(0);
  const apply = (value: Metadata) => { setMetadata(value); onMetadata?.(value); };
  useEffect(() => { let current = true; setLoaded(!port);
    if (port) void port.read().then(value => { if (current) { apply(value); setLoaded(true); setError(""); } }, error => { if (current) setError(String(error)); });
    return () => { current = false; };
  }, [port, retry]);
  const change = async (id: string, action: Parameters<ThreadMetadataPort["change"]>[1], value?: boolean | string | null) => {
    if (!port || !loaded || busy) return;
    setBusy(true); setError("");
    try { apply(await port.change(id, action, value)); setMenu(undefined); setEditing(undefined); }
    catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  const selectedUpdated = threads.find(t => t.threadId === active)?.updatedAt;
  useEffect(() => {
    if (port && loaded && ready && active) void port.change(active, "viewed").then(apply, error => setError(String(error)));
  }, [port, loaded, ready, active, selectedUpdated]);
  const match = (t: ThreadSummary) => (metadata[t.threadId]?.name ?? t.title).toLowerCase().includes(query.toLowerCase());
  const waiting = threads.filter(t => (t.pendingGates ?? 0) > 0 && match(t));
  const waitingIds = new Set(waiting.map(t => t.threadId));
  const pinned = threads.filter(t => metadata[t.threadId]?.pinned && !metadata[t.threadId]?.archived && !waitingIds.has(t.threadId) && match(t));
  const pinnedIds = new Set(pinned.map(t => t.threadId));
  const recent = threads.filter(t => !metadata[t.threadId]?.archived && !waitingIds.has(t.threadId) && !pinnedIds.has(t.threadId) && match(t));
  const archived = threads.filter(t => metadata[t.threadId]?.archived && !waitingIds.has(t.threadId) && match(t));
  const row = (thread: ThreadSummary) => {
    const m = metadata[thread.threadId], title = m?.name ?? thread.title, stamp = thread.updatedAt ? Date.parse(thread.updatedAt) : NaN;
    const unread = m?.unread === true || Number.isFinite(stamp) && (m?.viewedAt !== undefined && stamp > m.viewedAt);
    return <div role="listitem" key={thread.threadId} className={`desktop-thread-entry${active === thread.threadId ? " is-active" : ""}`}>
      <div className="desktop-thread-line"><ThreadRow thread={{ ...thread, title }} isActive={active === thread.threadId} navLocked={locked} now={now} onClick={() => onSelect(thread.threadId)} />
        {unread && <span className="desktop-unread" aria-label="Unread">●</span>}
        {port && <button type="button" className="desktop-row-menu" aria-label={`Conversation menu: ${title}`} aria-expanded={menu === thread.threadId} onClick={() => { setMenu(menu === thread.threadId ? undefined : thread.threadId); setEditing(undefined); }}><Icon name="more" size={14}/></button>}
      </div>
      {(thread.pendingGates ?? 0) > 0 && <span className="desktop-attention">{waitingLabel(thread.gateKinds)}</span>}
      {menu === thread.threadId && <div role="menu" aria-label={`Actions for ${title}`} className="desktop-thread-menu" onKeyDown={e => { if (e.key === "Escape") setMenu(undefined); }}>
        <button role="menuitem" disabled={busy || !loaded} onClick={() => void change(thread.threadId, "pin", !m?.pinned)}>{m?.pinned ? "Unpin" : "Pin"}</button>
        <button role="menuitem" disabled={busy || !loaded} onClick={() => { setName(title); setEditing(thread.threadId); }}>Rename locally</button>
        <button role="menuitem" disabled={busy || !loaded} onClick={() => void change(thread.threadId, unread ? "viewed" : "unread")}>{unread ? "Mark read" : "Mark unread"}</button>
        <button role="menuitem" disabled={busy || !loaded} onClick={() => void change(thread.threadId, "archive", !m?.archived)}>{m?.archived ? "Restore conversation" : "Archive"}</button>
        {m?.name && <button role="menuitem" disabled={busy} onClick={() => void change(thread.threadId, "name", null)}>Use original name</button>}
      </div>}
      {editing === thread.threadId && <form className="desktop-thread-rename" onSubmit={e => { e.preventDefault(); void change(thread.threadId, "name", name); }}>
        <label>Local name<input autoFocus aria-label="Local conversation name" maxLength={200} value={name} onChange={e => setName(e.target.value)} /></label>
        <button disabled={busy || !name.trim()} type="submit">Save</button><button type="button" onClick={() => setEditing(undefined)}>Cancel</button>
      </form>}
    </div>;
  };
  const group = (label: string, entries: ThreadSummary[], expanded: boolean) => <details className="desktop-thread-group" open={expanded || !!query}>
    <summary><Icon name="chev-r" size={11}/><span>{label}</span><small>{entries.length}</small></summary><div role="list" aria-label={`${label} conversations`}>{entries.map(row)}</div>
  </details>;
  return <>
    {error && <div role="alert" className="desktop-layout-error">{error} <button type="button" onClick={() => setRetry(v => v + 1)}>Retry preferences</button></div>}
    {waiting.length > 0 && group("Waiting for you", waiting, true)}
    {pinned.length > 0 && group("Pinned", pinned, true)}
    <div className="desktop-thread-group"><button type="button" className="desktop-project" aria-expanded={groupOpen} onClick={onGroupToggle}><Icon name="chev-r" size={11}/><span title={project}>{project}</span><small>{recent.length}</small></button>
      {groupOpen && <>
      <div role="list" aria-label={`${project} conversations`}>{(showAll || query ? recent : recent.filter((t, i) => i < 8 || t.threadId === active)).map(row)}
        {recent.length > 8 && !query && <button type="button" className="desktop-show-more" onClick={() => setShowAll(v => !v)}>{showAll ? "Show less" : `Show ${recent.filter((t, i) => i >= 8 && t.threadId !== active).length} more`}</button>}
        {!recent.length && <p className="desktop-empty">{query ? "No conversations match." : "No other conversations."}</p>}
      </div></>}
    </div>
    {archived.length > 0 && group("Archived", archived, false)}
  </>;
}
