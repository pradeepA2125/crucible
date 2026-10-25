import { CardShell } from "../components/shared/CardShell";
import { useEffect, useRef } from "react";
import { Icon, type IconName } from "../components/Icon";
import type { WorkspaceTool } from "./workspaceTabs";
const TOOL: Record<WorkspaceTool, { label: string; icon: IconName }> = { terminal: {label:"Terminal & output",icon:"term"}, files: { label: "Files", icon: "folder" }, changes: { label: "Changes", icon: "diff" }, agents: { label: "Agents", icon: "spark" } };
interface Props { focusRequested: boolean; focusHandled(): void; hasFiles: boolean; recent: string[]; changed: string[]; choose(tool: WorkspaceTool): void; open(path: string): void; }
export function WorkspaceLauncher({ focusRequested, focusHandled, hasFiles, recent, changed, choose, open }: Props) {
  const rows = useRef<(HTMLButtonElement | null)[]>([]);
  // Changes reads the conversation's own edit records, so it needs no file access.
  const tools: WorkspaceTool[] = hasFiles ? ["files", "changes", "agents", "terminal"] : ["changes", "agents", "terminal"];
  useEffect(() => { if (focusRequested) { rows.current[0]?.focus(); focusHandled(); } }, [focusRequested, focusHandled]);
  const suggested = [...new Set([...changed, ...recent])].slice(0, 20);
  return <section role="region" aria-label="New tab" className="desktop-new-tab">
    <CardShell icon="panel-right" title="Workspace tools"><div className="desktop-launcher-card"><p className="desktop-shortcut-hints">⌘/Ctrl K · Search files and actions &nbsp; ⌘/Ctrl F · Find visible text</p>
    <div className="desktop-tool-rows">{tools.map((tool, index) => <button key={tool} ref={node => { rows.current[index] = node; }} type="button" onClick={() => choose(tool)} onKeyDown={event => {
      let next: number;
      if (event.key === "ArrowDown") next = (index + 1) % tools.length;
      else if (event.key === "ArrowUp") next = (index + tools.length - 1) % tools.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tools.length - 1;
      else return;
      event.preventDefault(); rows.current[next]?.focus();
    }}><Icon name={TOOL[tool].icon} size={17} /><span>{TOOL[tool].label}</span></button>)}</div></div></CardShell>
    {suggested.length > 0 && <><h2>Suggested</h2><ul>{suggested.map(path => <li key={path}><button type="button" title={path} onClick={() => open(path)}><Icon name="file" size={14} /><span>{path}</span><small>{changed.includes(path) ? "Conversation edit" : "Recently opened"}</small></button></li>)}</ul></>}
  </section>;
}
