import {createPortal} from "react-dom";
import {CardShell} from "../components/shared/CardShell";
import {BtnGhost,BtnDanger} from "../components/shared/buttons";
import { Icon } from "../components/Icon";
import {useModalFocus} from "../components/shared/useModalFocus";
import { useEffect, useState, useRef, useCallback } from "react";
import { vscode } from "../vscodeApi";
export interface RecentProject { id:string;root:string;name:string;missing:boolean;state?:"idle"|"busy"|"unknown";paused?:boolean;attention?:Array<{threadId:string;title:string;gates:number}>; }
/** Host-owned recents and activity only; never attaches background conversations. */
export function Projects({locked=false}:{locked?:boolean}) {
  const [projects,setProjects]=useState<RecentProject[]>([]),[selected,setSelected]=useState<string|null>(null),[error,setError]=useState("");
  const [removing,setRemoving]=useState<RecentProject|null>(null);
  const removeOpener=useRef<HTMLElement|null>(null),modal=useRef<HTMLDivElement>(null),close=useCallback(()=>setRemoving(null),[]);useModalFocus(modal,close,removeOpener,!!removing);
  useEffect(()=>{
    const receive=(event:MessageEvent)=>{const m=event.data;
      if(m?.type==="desktop/projects" && Array.isArray(m.projects)){setProjects(m.projects);setSelected(m.selected);setError("");}
      if(m?.type==="desktop/projectsError")setError(m.message);
    };
    window.addEventListener("message",receive);vscode.postMessage({type:"desktop/projectsRead"} as never);
    return()=>window.removeEventListener("message",receive);
  },[]);
  const send=(type:string,p:RecentProject)=>{setError("");vscode.postMessage({type,projectId:p.id} as never);};
  return <section className="desktop-recent-projects" aria-label="Projects">
    <div className="desktop-projects-label"><span className="desktop-section-title">Projects</span><span>{projects.length}/32</span></div>
    {projects.map(p=><div className={`desktop-recent-project ${selected===p.id?"is-selected":""}`} key={p.id}>
      <div className="desktop-project-row"><button type="button" disabled={locked||selected===p.id||p.missing} title={p.root} aria-current={selected===p.id?"page":undefined} onClick={()=>send("desktop/selectProject",p)}>
        <span className="desktop-project-avatar" aria-hidden="true"><Icon name="folder" size={15}/></span>
        <span className="desktop-project-copy"><span className="desktop-project-name">{p.name}</span><small><span className={`desktop-project-dot ${p.state??"unloaded"}`} aria-hidden="true" />{p.missing?"Missing":p.paused?"Paused":p.state==="busy"?"Working / review":p.state==="unknown"?"Status unknown":selected===p.id?"Open":p.state==="idle"?"Ready":"Not opened"}</small></span>
      </button><details><summary aria-label={`Actions for ${p.name}`}><Icon name="more" size={14}/></summary><div>
        {p.missing&&<button disabled={locked} onClick={()=>send("desktop/locateProject",p)}>Locate…</button>}
        <button disabled={locked||selected===p.id} onClick={event=>{removeOpener.current=event.currentTarget;setRemoving(p);}}>Remove from Recents…</button>
      </div></details></div>
      {selected!==p.id && p.attention?.map(t=><button key={t.threadId} disabled={locked} className="desktop-project-attention" onClick={()=>vscode.postMessage({type:"desktop/selectProject",projectId:p.id,threadId:t.threadId} as never)} title={t.threadId}>{t.title||"Conversation"} · {t.gates} awaiting review</button>)}
    </div>)}
    {error&&<p role="alert">{error}</p>}
    {removing&&createPortal(<div className="desktop-palette-scrim desktop-dialog-scrim"><div ref={modal} role="dialog" aria-modal="true" aria-label="Remove project" className="desktop-access-dialog"><CardShell icon="folder" title={`Remove ${removing.name} from Recents?`}><div className="desktop-access-body"><p className="desktop-access-path">{removing.root}</p><p>Its repository and saved conversations are kept. Local unsent drafts for this project will be removed.</p><div className="desktop-dialog-actions"><BtnGhost onClick={close}>Cancel</BtnGhost><BtnDanger onClick={()=>{vscode.postMessage({type:"desktop/removeProject",projectId:removing.id,confirmed:true} as never);setRemoving(null);}}>Remove local drafts and recent entry</BtnDanger></div></div></CardShell></div></div>,document.body)}
  </section>;
}
