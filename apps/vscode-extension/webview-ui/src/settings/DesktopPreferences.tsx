import { useEffect, useState } from "react";
import { SectionHeader } from "./SectionHeader";
import { CardShell } from "../components/shared/CardShell";
import { BtnGhost } from "../components/shared/buttons";
import { vscode } from "./vscodeApi";
interface Status { preferences:{notifications:"off"|"attention"|"all";keepAwake:boolean};supported:boolean;awake:boolean;delivery:string; }
export function DesktopPreferences({hostDesktop=false}:{hostDesktop?:boolean}){
  const [state,setState]=useState<Status>(),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  useEffect(()=>{if(!hostDesktop)return;const receive=(e:MessageEvent)=>{if(e.data?.type==="desktop/lifecycleStatus"){setState(e.data);setBusy(false);setError("");}if(e.data?.type==="desktop/lifecycleError"){setError(e.data.message);setBusy(false);}};
    window.addEventListener("message",receive);vscode.postMessage({type:"desktop/lifecycleRead"} as never);return()=>window.removeEventListener("message",receive);},[hostDesktop]);
  if(!hostDesktop)return null;
  const update=(value:Partial<Status["preferences"]>)=>{if(!state)return;setBusy(true);vscode.postMessage({type:"desktop/lifecycleSet",preferences:{...state.preferences,...value}} as never);};
  return <section aria-label="Desktop preferences"><SectionHeader title="Desktop" description="Decide how Crucible behaves when you step away. These preferences apply only on this computer."/>
    <div className="flex flex-col gap-4"><CardShell icon="info" title="Notifications"><div className="p-4 pt-1 flex flex-col gap-3">
      <p className="text-caption text-text-3">Get a quiet update when a background project needs approval or finishes work. Notifications stay silent while you’re using Crucible.</p>
      <label className="text-caption text-text-2 flex flex-col gap-2">Notify me about<select className="rounded-control border border-border-strong bg-surface-2 px-3 py-2 text-text" value={state?.preferences.notifications??"off"} disabled={!state||busy||!state.supported} onChange={e=>update({notifications:e.target.value as Status["preferences"]["notifications"]})}><option value="off">Off</option><option value="attention">Approval requests</option><option value="all">Approval requests and completed work</option></select></label>
      <div className="flex items-center gap-3 flex-wrap"><BtnGhost disabled={!state||busy||!state.supported||state.preferences.notifications==="off"} onClick={()=>{setBusy(true);vscode.postMessage({type:"desktop/lifecycleTest"} as never);}}>Send test notification</BtnGhost><span role="status" className="text-caption text-text-3">{state?.delivery??"Checking notification support…"}</span></div>
      {state&&!state.supported&&<p className="text-caption text-text-3">System notifications are unavailable on this computer.</p>}
    </div></CardShell>
    <CardShell icon="chip" title="Keep work running"><div className="p-4 pt-1 flex flex-col gap-3"><label className="flex items-start gap-3 text-caption text-text-2"><input type="checkbox" checked={state?.preferences.keepAwake??false} disabled={!state||busy} onChange={e=>update({keepAwake:e.target.checked})}/><span>Keep this computer awake during work controlled by Crucible</span></label>
      <p className="text-caption text-text-3">Off by default. Applies while owned turns, agents, teams, commands or Your shell are running. Your display can still sleep. Activity that cannot be verified does not keep the computer awake.</p>
      <p role="status" className="text-caption text-text-3">{state?.awake?"Keeping the computer awake for active work.":"Crucible is not preventing sleep."}</p></div></CardShell>
    <CardShell icon="folder" title="Closing the window"><div className="p-4 pt-1 text-caption text-text-3">Closing or quitting offers Cancel, Keep running, and Quit. Keep running hides the window while Crucible remains open. Reopen it from the Dock to continue.</div></CardShell>
    {error&&<p role="alert" className="text-caption text-red">{error}</p>}</div></section>;
}
