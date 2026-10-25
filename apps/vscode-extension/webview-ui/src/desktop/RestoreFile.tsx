import { createPortal } from "react-dom";
import { useEffect,useRef,useState } from "react";
import { CardShell } from "../components/shared/CardShell";
import { BtnGhost,BtnDanger } from "../components/shared/buttons";
import { useModalFocus } from "../components/shared/useModalFocus";
import type { RestoreAction,RestoreResult } from "../../../../desktop/src/restore-protocol";
import type { RestorePort } from "./restoreClient";
type Preview=Extract<RestoreResult,{kind:"preview"}>;
type History=Extract<RestoreResult,{kind:"history"}>;
/** Shared React confirmation; no file bytes or filesystem mutation in this view. */
export function RestoreFile({run,action,path,onChanged,label="Restore file…"}:{run?:RestorePort;action?:RestoreAction;path?:string;onChanged:()=>void;label?:string}){
 const [preview,setPreview]=useState<Preview>(),[busy,setBusy]=useState(false),[error,setError]=useState(""),[done,setDone]=useState<Extract<RestoreResult,{kind:"applied"}>>();
 const root=useRef<HTMLDivElement>(null),opener=useRef<HTMLElement|null>(null),mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const close=()=>{if(!busy)setPreview(undefined);};useModalFocus(root,close,opener,!!preview);
 const request=async(value:RestoreAction)=>{
  if(!run||busy)return;setBusy(true);setError("");
  try{const result=await run(value);if(result.kind==="preview"){if(path&&result.path!==path)throw new Error("File recovery returned a different file. Refresh.");setPreview(result);}
   else if(result.kind==="applied"){const fallback=document.querySelector<HTMLElement>(".restore-history > summary");setDone(result);setPreview(undefined);onChanged();if(fallback)requestAnimationFrame(()=>{if(fallback.isConnected)fallback.focus();});}}
  catch(e){setError(e instanceof Error?e.message:"File recovery could not finish. Check history.");if(value.operation==="apply")setPreview(undefined);}
  finally{if(mounted.current)setBusy(false);}
 };
 if(!run)return null;
 return <div className="restore-file-controls" onClickCapture={event=>{const trigger=event.target instanceof Element?event.target.closest("button"):null;if(!preview&&trigger instanceof HTMLElement)opener.current=trigger;}}>
  <BtnGhost disabled={busy||!action} onClick={()=>action&&void request(action)}>{busy?"Checking file…":label}</BtnGhost>
  {!action&&<span className="text-text-3 text-caption">Open a complete text diff to restore this file.</span>}
  {done&&<span role="status">{done.action==="undo"?"Restore undone.":"File restored."} {done.action!=="undo"&&<BtnGhost disabled={busy} onClick={()=>void request({operation:"undo",receipt:done.receipt})}>Undo restore…</BtnGhost>}</span>}
  {error&&<p role="status" className="restore-error">{error}</p>}
  {preview&&createPortal(<div className="restore-overlay"><div ref={root} role="dialog" aria-modal="true" aria-labelledby={`restore-title-${preview.token}`} tabIndex={-1} className="restore-dialog"><CardShell icon="retry" title="File recovery">
   <div className="restore-dialog-body"><h2 id={`restore-title-${preview.token}`}>{preview.action==="undo"?"Undo file restore?":"Restore retained file?"}</h2>
   <p className="restore-path"><bdi>{preview.path}</bdi></p>
   <p>{preview.effect==="delete"?preview.action==="undo"?"Delete this file to restore its pre-restore state.":"Delete this file to match the retained state.":preview.effect==="create"?preview.action==="undo"?"Recreate this file from its pre-restore recovery copy.":"Recreate this file from retained content.":preview.action==="undo"?"Replace this file’s current content with its pre-restore recovery copy.":"Replace this file’s current content with the retained content."}</p>
   <p>{preview.currentBytes.toLocaleString()} current bytes → {preview.targetBytes.toLocaleString()} {preview.action==="undo"?"recovery":"retained"} bytes. {preview.action==="undo"?"Both file states remain in the existing recovery record.":"A recovery copy is saved before changing the file."} Conversation history stays intact.</p>
   <p className="text-text-3">Close external editors or other writers first. Their writes cannot be fully excluded; changed content makes this preview expire.</p>
   <div className="restore-dialog-actions"><BtnGhost disabled={busy} onClick={close}>Cancel</BtnGhost><BtnDanger disabled={busy} onClick={()=>void request({operation:"apply",token:preview.token})}>{busy?"Restoring…":preview.action==="undo"?"Undo restore":preview.effect==="delete"?"Delete file":"Restore file"}</BtnDanger></div>
   </div></CardShell></div></div>,document.body)}
 </div>;
}
export function FileRecoveryHistory({run,onChanged,latest}:{run?:RestorePort;onChanged:()=>void;latest?:Extract<RestoreResult,{kind:"applied"}>}){
 const [history,setHistory]=useState<History>(),[error,setError]=useState(""),[loading,setLoading]=useState(false);
 if(!run)return null;
 const load=async()=>{setLoading(true);setError("");try{const r=await run({operation:"history"});if(r.kind!=="history")throw new Error("Recovery history was unavailable.");setHistory(r);}catch(e){setError(e instanceof Error?e.message:"Recovery history unavailable.");}finally{setLoading(false);}};
 return <>{latest&&<div className="restore-history" role="status"><p>{latest.action==="undo"?"Restore undone":"File restored"}: <bdi>{latest.path}</bdi></p>{latest.action!=="undo"&&<RestoreFile run={run} path={latest.path} action={{operation:"undo",receipt:latest.receipt}} label="Undo last restore…" onChanged={onChanged}/>}</div>}<details className="restore-history"><summary>File recovery history</summary><p className="text-caption text-text-3">Saved before each restore in this conversation; Undo reuses that recovery record. An interrupted operation may be unconfirmed; inspect its file before trying again. Undo refuses newer content.</p><BtnGhost disabled={loading} onClick={()=>void load()}>{loading?"Loading…":"Refresh recovery history"}</BtnGhost>
 {error&&<p role="status">{error}</p>}{history&&!history.entries.length&&<p>No file recovery records yet.</p>}
 {history?.entries.map(e=><div key={e.id} className="restore-history-entry"><div><bdi>{e.path}</bdi><p className="text-caption text-text-3">{new Date(e.created).toLocaleString()} · {e.action==="undo"?"Undo":"Restore"} · {e.state==="applied"?"Applied":e.state==="undone"?"Undone":"Completion unconfirmed"}</p></div>{e.state!=="undone"&&<RestoreFile run={run} path={e.path} action={{operation:"undo",receipt:e.id}} label="Preview Undo…" onChanged={()=>{onChanged();void load();}}/>}</div>)}</details></>;
}
