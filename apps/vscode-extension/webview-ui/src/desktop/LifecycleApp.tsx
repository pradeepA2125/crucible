import { useCallback, useEffect, useRef, useState } from "react";
import { CardShell } from "../components/shared/CardShell";
import { BtnPrimary, BtnGhost, BtnDanger } from "../components/shared/buttons";
import { useModalFocus } from "../components/shared/useModalFocus";
import type { QuitPreview } from "../../../../desktop/src/lifecycle-protocol";
export default function LifecycleApp({send}:{send:(message:Record<string,unknown>)=>void}) {
  const [preview,setPreview]=useState<QuitPreview>(),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const dialog=useRef<HTMLDivElement>(null);
  const choose=useCallback((choice:string)=>{if(!preview||busy)return;setBusy(true);send({type:"desktop/lifecycleChoice",snapshotId:preview.id,choice});},[preview,busy,send]);
  const cancel=useCallback(()=>choose("cancel"),[choose]);
  useModalFocus(dialog,cancel,undefined,!!preview);
  useEffect(()=>{const receive=(e:MessageEvent)=>{if(e.data?.type==="desktop/quitPreview"){setPreview(e.data.preview);setBusy(false);setError("");}if(e.data?.type==="desktop/lifecycleError"){setError(e.data.message);setBusy(false);}};
    window.addEventListener("message",receive);send({type:"desktop/lifecycleReady"});return()=>window.removeEventListener("message",receive);},[send]);
  useEffect(()=>{dialog.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();},[preview?.id]);
  const recovering=preview?.phase==="cleanup-error";
  return <main className="lifecycle-page"><div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="quit-title" tabIndex={-1} className="lifecycle-dialog">
    <div className="lifecycle-heading"><span className="text-meta text-text-3">CRUCIBLE DESKTOP</span><h1 id="quit-title">{recovering?"Finish closing Crucible":preview?.phase==="draft-error"?"Your latest draft needs attention":"Keep your work running?"}</h1>
    <p>{recovering?"Some resources could not close. Retry to stop the remaining owned processes.":"Keep Crucible running in the background, or quit and stop work controlled by this app."}</p></div>
    {!preview&&<p role="status">Checking open projects…</p>}
    <div className="lifecycle-projects">{preview?.projects.map(project=><CardShell key={project.id} icon="folder" title={project.name} subtitle={project.state==="unknown"?"Activity unavailable":project.owner?"Controlled by this app":"Observed or unconfigured"}>
      <div className="lifecycle-project-body">
        {project.state==="unknown"&&<p className="text-amber">Activity could not be verified. Quitting stops only this app’s owned processes.</p>}
        {!project.threads.some(t=>t.turn_active||t.gates||t.agents.length||t.teams.length||t.sessions.length)&&!project.shells.length&&project.state!=="unknown"&&<p className="text-text-3">{project.state==="busy"?"A project action is still settling.":"No active work observed."}</p>}
        {project.threads.filter(t=>t.turn_active||t.gates||t.agents.length||t.teams.length||t.sessions.length).map(thread=><div key={thread.thread_id} className="lifecycle-thread"><strong>{thread.title||"Untitled conversation"}</strong><div className="lifecycle-tags">{thread.turn_active&&<span>Turn running</span>}{thread.gates>0&&<span>{thread.gates} awaiting approval</span>}</div>
          {thread.agents.map(a=><p key={a.id}>Agent · {a.label}</p>)}{thread.teams.map(a=><p key={a.id}>Team · {a.label}</p>)}{thread.sessions.map(a=><p key={a.id}>Command · {a.label}</p>)}</div>)}
        {project.shells.map(shell=><p key={shell.id}>Your shell · {shell.label}</p>)}
        {!project.owner&&project.threads.length>0&&<p className="text-text-3">A different client’s backend will stay running.</p>}
      </div></CardShell>)}</div>
    {(preview?.error||error)&&<div role="alert" className="lifecycle-error">{error||preview?.error}</div>}
    <p className="lifecycle-footnote">Background mode keeps this app open. Work controlled by this app stops when you quit. Unsent drafts survive quitting only when encrypted draft storage is enabled.</p>
    <div className="lifecycle-actions">{!recovering&&<BtnGhost disabled={busy||!preview} onClick={cancel}>Cancel</BtnGhost>}{!recovering&&<BtnPrimary disabled={busy||!preview} onClick={()=>choose("background")}>Keep running</BtnPrimary>}
      <BtnDanger disabled={busy||!preview} onClick={()=>choose("quit")}>{busy?"Checking…":preview?.phase==="draft-error"?"Retry save and quit":recovering?"Retry cleanup and quit":"Quit Crucible"}</BtnDanger>
      {preview?.phase==="draft-error"&&<BtnDanger disabled={busy} onClick={()=>choose("discard")}>Discard unsaved edits and quit</BtnDanger>}</div>
  </div></main>;
}
