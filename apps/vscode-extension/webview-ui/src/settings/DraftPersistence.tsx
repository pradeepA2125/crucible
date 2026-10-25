import { SectionHeader } from "./SectionHeader";
import {useModalFocus} from "../components/shared/useModalFocus";
import { useEffect, useState, useRef, useCallback } from "react";
import { vscode } from "./vscodeApi";
export function DraftPersistence({hostDesktop=false}:{hostDesktop?:boolean}){
  const [desktop,setDesktop]=useState(false),[state,setState]=useState<{enabled:boolean;available:boolean;legacy:boolean;recovery:boolean;project?:{name:string;root:string}}>(),[error,setError]=useState("");
  const [confirm,setConfirm]=useState<"legacy"|"recovery"|null>(null),modal=useRef<HTMLDivElement>(null),close=useCallback(()=>setConfirm(null),[]);
  useModalFocus(modal,close,undefined,!!confirm);
  useEffect(()=>{
    const receive=(event:MessageEvent)=>{const m=event.data;
      if(m?.type==="themeChanged" && m.host==="desktop"){setDesktop(true);vscode.postMessage({type:"desktop/persistenceRead"} as never);}
      if(m?.type==="desktop/persistence"){setDesktop(true);setState(m);setError("");}
      if(m?.type==="desktop/persistenceError")setError(m.message);
    };window.addEventListener("message",receive);
    if(hostDesktop){setDesktop(true);vscode.postMessage({type:"desktop/persistenceRead"} as never);}
    // The existing shared theme event is the host discriminator; VS Code never sends desktop commands.
    return()=>window.removeEventListener("message",receive);
  },[hostDesktop]);
  if(!desktop)return null;
  return <section aria-label="Desktop drafts">
    <SectionHeader title="Drafts" description="Manage unsent desktop messages. Drafts stay separate for each project and conversation." />
    <div className="rounded-[10px] border border-border p-4">
    <h2 className="mb-2 text-sm font-medium">Encrypted draft storage</h2>
    <p className="mb-3 text-xs text-text-3">Keep unsent text and images after quitting, encrypted with your OS keychain. Off by default.</p>
    <label className="flex gap-2 text-sm"><input type="checkbox" checked={state?.enabled??false} disabled={!state || !state.enabled&&!state.available} onChange={e=>vscode.postMessage({type:"desktop/persistenceSet",enabled:e.target.checked} as never)}/>Save encrypted drafts on this computer</label>
    {state&&!state.available&&<p className="mt-2 text-xs text-text-3">Secure storage is unavailable. Unlock your OS keychain. Plaintext storage is never used.</p>}
    <p className="mt-2 text-xs text-text-3">Turning this off removes saved draft copies and keeps your current in-memory drafts. Interrupted sends remain quarantined until you explicitly recover their text after checking saved history.</p>
    {state?.recovery&&<button className="mt-3 text-sm underline" onClick={()=>setConfirm("recovery")}>Review interrupted draft recovery…</button>}
    {state?.legacy&&<div className="mt-4 text-xs"><p>Previous desktop provider keys need a project assignment. Other projects cannot use them.</p><button className="mt-2 underline" onClick={()=>setConfirm("legacy")}>Assign previous encrypted keys to this project…</button></div>}
    {confirm&&<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"><div ref={modal} role="alertdialog" aria-modal="true" aria-label={confirm==="recovery"?"Recover interrupted send":"Assign previous keys"} className="max-h-[80vh] w-full max-w-[560px] overflow-y-auto rounded-[12px] border border-border bg-surface p-5 text-sm">
      <h2>{confirm==="recovery"?"Recover an interrupted send?":`Assign previous keys to ${state?.project?.name??"this project"}?`}</h2>
      <p className="my-3 break-all text-xs">{state?.project?.root}</p>
      <p className="my-3 text-xs">{confirm==="recovery"?"The original message may already have reached the backend. Check this project's saved conversation history first. Recovering text does not resend it, but sending it later may duplicate work. Confirm only after reviewing the original conversation.":"The old app used one shared encrypted key file. Its original project cannot be proven. Confirm that these keys belong in this named project. Other projects will not receive them. The original encrypted file is kept."}</p>
      <div className="flex flex-wrap gap-3"><button onClick={close}>{confirm==="recovery"?"Keep quarantined":"Cancel"}</button><button onClick={()=>{vscode.postMessage({type:confirm==="recovery"?"desktop/recoverDraft":"desktop/importLegacyKeys",confirmed:true} as never);setConfirm(null);}}>{confirm==="recovery"?"I checked history — recover text":"Import into this project"}</button></div>
    </div></div>}
    {error&&<p className="mt-2 text-xs" role="alert">{error}</p>}
    </div>
  </section>;
}
