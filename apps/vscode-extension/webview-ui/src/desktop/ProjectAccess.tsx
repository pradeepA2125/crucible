import {createPortal} from "react-dom";
import {CardShell} from "../components/shared/CardShell";
import {BtnGhost,BtnPrimary,BtnDanger} from "../components/shared/buttons";
import {useCallback,useEffect,useRef,useState} from "react";
import {useModalFocus} from "../components/shared/useModalFocus";
import {vscode} from "../vscodeApi";
interface Access {state:"owner"|"observer"|"unavailable";workspace:string;processId?:string;pid?:number;client?:string;trusted?:boolean;message:string;}
export function ProjectAccess({threadId,title}:{threadId:string;title:string}){
 const [access,setAccess]=useState<Access>({state:"unavailable",workspace:"",message:"Checking project ownership…"}),[open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(""),[name,setName]=useState(title);
 const opener=useRef<HTMLButtonElement>(null),root=useRef<HTMLDivElement>(null),close=useCallback(()=>{if(!busy)setOpen(false);},[busy]);useModalFocus(root,close,opener,open);
 useEffect(()=>{const receive=(event:MessageEvent)=>{const m=event.data;if(m?.type==="desktop/access"){setAccess(m);setBusy(false);}if(m?.type==="desktop/accessError"){setError(String(m.message));setBusy(false);}if(m?.type==="desktop/sharedRenamed"){setError("");setBusy(false);}};window.addEventListener("message",receive);vscode.postMessage({type:"desktop/accessRead"} as never);return()=>window.removeEventListener("message",receive);},[]);
 useEffect(()=>setName(title),[threadId,title]);
 useEffect(()=>{if(!busy)return;const timer=setTimeout(()=>{setBusy(false);setError("The result is unknown. Refresh project access before retrying.");},6000);return()=>clearTimeout(timer);},[busy]);
 const action=(message:unknown)=>{setBusy(true);setError("");vscode.postMessage(message as never);};
 return <><button ref={opener} type="button" className="desktop-access-button" onClick={()=>{setOpen(true);setError("");vscode.postMessage({type:"desktop/accessRead"} as never);}}>{access.state==="owner"?`Owner · ${access.trusted?"Trusted":"Untrusted"}`:access.state==="observer"?"Observer · Read only":"Project access"}</button>
 {open && createPortal(<div className="desktop-palette-scrim desktop-dialog-scrim"><div ref={root} role="dialog" aria-modal="true" aria-label="Project access" className="desktop-access-dialog"><CardShell icon="shield" title="Project access" trailing={<BtnGhost disabled={busy} onClick={close}>Close</BtnGhost>}><div className="desktop-access-body"><p className="desktop-access-path">{access.workspace}</p><p>{access.state==="owner"?"Controlling client":access.state==="observer"?"Read-only observer":"Ownership unavailable"}{access.client?` · ${access.client}`:""}{access.pid?` · PID ${access.pid}`:""}</p><p>{access.message}</p>
 {error&&<p role="alert">{error}</p>}
 {access.state==="owner"&&<><p>Trust applies to this running project session. Approval mode returns to Ask for approval on every trust change. Trusting does not select Full access. Commands already running are not undone; change trust while the project is idle.</p>{access.trusted?<BtnDanger disabled={busy} onClick={()=>action({type:"desktop/trust",processId:access.processId,trusted:false})}>Revoke trust and ask for approval</BtnDanger>:<BtnGhost disabled={busy} onClick={()=>action({type:"desktop/trust",processId:access.processId,trusted:true})}>Trust this project session</BtnGhost>}
 {threadId&&<fieldset disabled={busy}><legend>Shared conversation title</legend><p>Visible to every client. A desktop-local display name remains separate.</p><input aria-label="Shared conversation title" maxLength={200} value={name} onChange={e=>setName(e.target.value)}/><BtnPrimary disabled={!name.trim()} onClick={()=>action({type:"desktop/renameShared",threadId,title:name.trim()})}>Save shared title</BtnPrimary></fieldset>}</>}
 <div className="desktop-dialog-actions"><BtnGhost disabled={busy} onClick={()=>{setError("");vscode.postMessage({type:"desktop/accessRead"} as never);}}>Refresh access</BtnGhost></div></div></CardShell></div></div>,document.body)}
 </>;
}
