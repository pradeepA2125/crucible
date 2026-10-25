import { useRef } from "react";
import { Icon } from "../components/Icon";
import type { ProjectFiles } from "./useProjectFiles";
interface Props { path: string; wrap: boolean; source: boolean; setWrap(value: boolean): void; setSource(value: boolean): void; model: ProjectFiles; }
export function FileToolbar({ path, wrap, source, setWrap, setSource, model }: Props) {
  const menu = useRef<HTMLDetailsElement>(null), trigger = useRef<HTMLElement>(null);
  const action = (type: "desktop/filesCopyPath" | "desktop/filesReveal" | "desktop/filesChooseEditor") => { if (menu.current) menu.current.open = false; void model.action(type, type === "desktop/filesChooseEditor" ? undefined : path); };
  return <div className="desktop-file-toolbar">
    <nav aria-label="File breadcrumb" title={path}>{path}</nav>
    <div className="desktop-file-controls">
      {/\.(md|markdown)$/i.test(path) && <button type="button" className="desktop-source-toggle" title={source ? "Render Markdown" : "View Markdown source"} onClick={() => setSource(!source)}>{source ? "Preview" : "View source"}</button>}
      <button type="button" className="desktop-icon" aria-label="Wrap lines" title="Wrap lines" aria-pressed={wrap} onClick={() => setWrap(!wrap)}><Icon name="wrap" size={16} /></button>
      <button type="button" className="desktop-icon" aria-label="Reload" title="Reload preview" onClick={() => void model.loadFile(path)}><Icon name="retry" size={16} /></button>
      <button type="button" className="desktop-icon" aria-label="Open in editor" title="Open in selected editor" onClick={() => void model.action("desktop/filesOpenEditor", path)}><Icon name="expand" size={16} /></button>
      <details ref={menu} className="desktop-file-actions" onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); if (menu.current) menu.current.open = false; trigger.current?.focus(); } }}>
        <summary role="button" ref={trigger} aria-label="File actions" title="More file actions"><Icon name="more" size={16} /></summary>
        <div role="group" aria-label="File actions"><button type="button" onClick={() => action("desktop/filesCopyPath")}><Icon name="copy" size={14} />Copy path</button><button type="button" onClick={() => action("desktop/filesReveal")}><Icon name="folder" size={14} />Reveal</button><button type="button" onClick={() => action("desktop/filesChooseEditor")}><Icon name="gear" size={14} />Choose editor…</button><p>Editor choice applies to this app. Choose an installed editor outside the workspace.</p></div>
      </details>
    </div>
  </div>;
}
