import { useEffect,useState } from "react";
import { CardShell } from "../components/shared/CardShell";
import { BtnPrimary } from "../components/shared/buttons";
import { Icon } from "../components/Icon";
import { vscode } from "../vscodeApi";
import { Projects } from "./Projects";
/** The existing projectless document, composed from the same React/theme system. */
export function DesktopWelcome(){
 const [notice,setNotice]=useState("");
 useEffect(()=>{const receive=(event:MessageEvent)=>{if(event.data?.type==="desktop/status"&&typeof event.data.text==="string")setNotice(event.data.text);};window.addEventListener("message",receive);return()=>window.removeEventListener("message",receive);},[]);
 return <div className="desktop-welcome"><aside className="desktop-welcome-sidebar" aria-label="Recent projects"><header className="desktop-welcome-brand"><span><Icon name="spark" size={20}/></span><strong>Crucible</strong><small>Desktop</small></header><Projects/><p className="desktop-welcome-sidebar-note">Your projects and conversations stay together.</p></aside>
  <main className="desktop-welcome-main"><div className="desktop-welcome-intro"><span className="desktop-section-title">YOUR WORKSPACE</span><h1>Your project. Your conversation.</h1><p>Open a local folder to continue a conversation, inspect files and review changes.</p></div>
   <CardShell icon="folder" title="Open a workspace"><div className="desktop-welcome-card"><p>Choose a project to use Crucible’s chat, composer and workspace tools. Each project keeps its own conversations and drafts.</p><BtnPrimary icon="plus" onClick={()=>vscode.postMessage({type:"desktop/openProject"} as never)}>Open project…</BtnPrimary></div></CardShell>
   <p role="status" className="desktop-welcome-notice">{notice||"Select a recent project, or open a folder to get started."}</p>
  </main></div>;
}
