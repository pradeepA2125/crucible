import { Icon } from "../components/Icon";
import { CardShell } from "../components/shared/CardShell";
import { useCallback, useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { SessionStrip } from "../components/messages/SessionStrip";
import type { LiveSessionItem, SessionTranscriptView } from "../types";
import type { TerminalInfo } from "../../../../desktop/src/terminal-protocol";
import { createTerminalClient, type TerminalPort } from "./terminalClient";
import { vscode } from "../vscodeApi";

function ShellScreen({port,session,error,onSession}:{port:TerminalPort;session:TerminalInfo;error(message:string):void;onSession(value:TerminalInfo):void}){
  const element=useRef<HTMLDivElement>(null),latest=useRef({error,onSession,session});latest.current={error,onSession,session};
  useEffect(()=>{
    if(!element.current)return;
    let disposed=false,offset=0,reading=false,queue=Promise.resolve(),queued=0;
    const style=getComputedStyle(document.documentElement),term=new Terminal({scrollback:5000,allowProposedApi:false,allowTransparency:false,fontSize:12,convertEol:false,screenReaderMode:true,disableStdin:session.status!=="running",theme:{background:style.getPropertyValue("--surface-0").trim()||"#181818",foreground:style.getPropertyValue("--text-1").trim()||"#ddd"}}),fit=new FitAddon();
    term.loadAddon(fit);
    const osc52=term.parser.registerOscHandler(52,()=>true),osc8=term.parser.registerOscHandler(8,()=>true);
    term.open(element.current);term.textarea?.setAttribute("aria-label","Your shell input");
    // No link/clipboard/OSC navigation addons are installed. Process output is terminal data only.
    const size=()=>{if(disposed||!element.current?.clientWidth)return;fit.fit();if(latest.current.session.status==="running")void port.request({operation:"resize",sessionId:session.id,cols:Math.max(2,Math.min(500,term.cols)),rows:Math.max(2,Math.min(300,term.rows))}).catch(e=>{if(!disposed)latest.current.error(String(e.message));});};
    const applyTheme=()=>{const css=getComputedStyle(document.documentElement);term.options.theme={background:css.getPropertyValue("--surface-0").trim()||"#181818",foreground:css.getPropertyValue("--text-1").trim()||"#ddd"};};
    const themeObserver=new MutationObserver(applyTheme);themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:["data-appearance","data-theme","style"]});
    const resize=new ResizeObserver(size);resize.observe(element.current);size();
    const data=term.onData(text=>{
      if(disposed)return;const bytes=new TextEncoder().encode(text).length;
      if(bytes>8192||queued+bytes>16384){latest.current.error("Terminal input is too large or busy. Paste smaller chunks; this input was not sent.");return;}
      queued+=bytes;
      // Serialize raw input, preserving literal backslashes and Ctrl-C; no model escape decoder.
      queue=queue.then(async()=>{if(disposed)return;let chunk="";for(const char of text){if(new TextEncoder().encode(chunk+char).length>4096){await port.request({operation:"input",sessionId:session.id,input:chunk});chunk="";}chunk+=char;}if(chunk)await port.request({operation:"input",sessionId:session.id,input:chunk});}).catch(e=>{if(!disposed)latest.current.error(e.message);}).finally(()=>{queued-=bytes;});
    });
    const read=async()=>{if(reading||disposed)return;reading=true;try{const result=await port.request({operation:"read",sessionId:session.id,offset});if(disposed)return;if(result.reset){term.reset();term.writeln("[Earlier output was dropped by the 1 MiB session limit.]");}if(result.data){const bytes=Uint8Array.from(atob(result.data),c=>c.charCodeAt(0));await new Promise<void>(resolve=>term.write(bytes,resolve));}if(disposed)return;offset=result.next??offset;if(result.session){term.options.disableStdin=result.session.status!=="running";latest.current.onSession(result.session);}}catch(e){if(!disposed){term.options.disableStdin=true;latest.current.error((e as Error).message);}}finally{reading=false;}};
    void read();const timer=setInterval(()=>void read(),150);
    return()=>{disposed=true;clearInterval(timer);resize.disconnect();themeObserver.disconnect();data.dispose();osc52.dispose();osc8.dispose();term.dispose();};
  },[port,session.id]);
  return <div className="desktop-shell-screen" ref={element} aria-label="Your shell terminal" />;
}
function TerminalSurface({threadId,port}:{threadId:string;port:TerminalPort}){
  const [mode,setMode]=useState<"agent"|"user">("agent"),[sessions,setSessions]=useState<TerminalInfo[]>([]),[selected,setSelected]=useState(""),[agents,setAgents]=useState<LiveSessionItem[]>([]),[transcripts,setTranscripts]=useState<Record<string,SessionTranscriptView|null>>({}),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  const live=useRef(true),readBusy=useRef(false),current=useRef(threadId);current.current=threadId;
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[port]);
  const update=useCallback((session:TerminalInfo)=>{setSessions(old=>old.map(s=>s.id===session.id?session:s));},[]);
  const refresh=useCallback(async()=>{if(readBusy.current)return;const thread=threadId;readBusy.current=true;try{const result=await port.request({operation:"list"});if(live.current)setSessions(result.sessions??[]);if(thread){const output=await port.request({operation:"agentList",threadId:thread});if(live.current&&current.current===thread)setAgents(output.agents??[]);}}catch(e){if(live.current&&current.current===thread)setError((e as Error).message);}finally{readBusy.current=false;}},[port,threadId]);
  useEffect(()=>{setAgents([]);setTranscripts({});void refresh();const timer=setInterval(()=>void refresh(),2000);return()=>clearInterval(timer);},[refresh]);
  const expand=useCallback((id:string)=>{const thread=threadId;void port.request({operation:"agentRead",threadId:thread,agentSessionId:id}).then(r=>{if(live.current&&current.current===thread)setTranscripts(old=>({...old,[id]:r.transcript??null}));},()=>{if(live.current&&current.current===thread)setTranscripts(old=>({...old,[id]:null}));});},[port,threadId]);
  const action=async(operation:"start"|"end"|"forget"|"interrupt")=>{if(busy)return;setBusy(true);setError("");try{const result=await port.request({operation,...(operation==="start"?{cols:100,rows:30}:{sessionId:selected})});if(!live.current)return;if(result.session)setSelected(result.session.id);await refresh();}catch(e){if(live.current)setError((e as Error).message);}finally{if(live.current)setBusy(false);}};
  const session=sessions.find(s=>s.id===selected)??sessions.at(-1),running=session?.status==="running";
  useEffect(()=>{if(session?.id&&selected!==session.id)setSelected(session.id);},[session?.id,selected]);
  return <section className="desktop-terminal-tool" aria-label="Terminal and output">
    <div className="desktop-terminal-toolbar desktop-terminal-modes">
      <div role="group" aria-label="Terminal provenance" className="desktop-terminal-segments">
        <button type="button" aria-pressed={mode==="agent"} onClick={()=>setMode("agent")}><Icon name="bolt" size={13}/>Agent output</button>
        <button type="button" aria-pressed={mode==="user"} onClick={()=>setMode("user")}><Icon name="term" size={13}/>Your shell</button>
      </div>
      <button type="button" className="desktop-terminal-refresh" aria-label="Refresh" title="Refresh sessions" disabled={busy} onClick={()=>void refresh()}><Icon name="retry" size={14}/></button>
    </div>
    {error&&<p role="alert" className="desktop-workspace-notice desktop-terminal-error">{error}</p>}
    <div className="desktop-terminal-card">
      {mode==="agent"?<CardShell icon="bolt" title="Agent command sessions" badge={<span className="desktop-terminal-badge">Read only</span>}>
        <div className="desktop-agent-output">
          <p className="desktop-terminal-context">Output from the current conversation</p>
          {agents.length?<SessionStrip items={agents} transcripts={transcripts} onExpand={expand}/>:<div className="desktop-terminal-empty"><span className="desktop-terminal-empty-icon"><Icon name="term" size={22}/></span><h3>No retained agent command sessions</h3><p>Command output from this conversation will appear here when the agent retains a session.</p></div>}
          <details className="desktop-terminal-details"><summary>Output limits and availability</summary><p>Initial cwd is not reported by this runtime. Shows up to 128 retained sessions and the last 16,000 output characters. Output may be unavailable after the agent releases a session or the backend restarts.</p></details>
        </div>
      </CardShell>:<CardShell icon="term" title="Your shell" subtitle={session?.shell} badge={<span className={`desktop-terminal-badge${running?" is-running":""}`}>{session?.status??"Not started"}</span>}>
        <div className="desktop-terminal-toolbar desktop-terminal-session-controls">
          <label><span>Session</span><select aria-label="Your shell session" value={session?.id??""} onChange={e=>setSelected(e.target.value)}>{!sessions.length&&<option value="">No shell started</option>}{sessions.map((s,i)=><option key={s.id} value={s.id}>Shell {i+1} · {s.status}{s.exitCode!==null?` (${s.exitCode})`:""}</option>)}</select></label>
          <div className="desktop-terminal-actions">
            <button type="button" className="desktop-terminal-launch" disabled={busy} onClick={()=>void action("start")}><Icon name="plus" size={12}/>Launch shell…</button>
            {running&&<button type="button" disabled={busy} onClick={()=>void action("interrupt")}>Interrupt</button>}
            {session?.hostAlive&&<button type="button" className="desktop-terminal-end" disabled={busy} onClick={()=>void action("end")}>End session</button>}
            {session&&!session.hostAlive&&<button type="button" disabled={busy} onClick={()=>void action("forget")}>Remove output</button>}
          </div>
        </div>
        {session?<>
          <div className="desktop-terminal-status"><p>{`Initial cwd: ${session.initialCwd}`}</p><p>{session.cols}×{session.rows} · Scrollback capped at 1 MiB / 5,000 lines{session.exitSignal?` · Signal ${session.exitSignal}`:session.exitCode!==null?` · Exit ${session.exitCode}`:""}</p>{session.error&&<p role="status">{session.error}</p>}</div>
          <ShellScreen key={session.id} port={port} session={session} error={setError} onSession={update}/>
        </>:<div className="desktop-terminal-empty"><span className="desktop-terminal-empty-icon"><Icon name="term" size={24}/></span><h3>A shell for your project</h3><p>Launch your own interactive shell in this project. This runs commands as you; it does not send agent commands or change agent approvals. Opening this view does not launch anything.</p></div>}
        <div className="desktop-terminal-footnote"><Icon name="info" size={13}/><div><p>Hiding or closing this tab keeps your shell running.</p><details className="desktop-terminal-details"><summary>Session behavior</summary><p>End session stops the shell and its owned jobs. App quit stops owned shells. Shells do not restart automatically. Manual shell writes can race agent edits.</p></details></div></div>
      </CardShell>}
    </div>
  </section>;
}

export function TerminalTool({threadId,port:provided}:{threadId:string;port?:TerminalPort}) {
  const [port,setPort]=useState<TerminalPort|undefined>(provided);
  useEffect(()=>{const active=provided??createTerminalClient(m=>vscode.postMessage(m as never));setPort(active);return()=>{if(!provided)active.dispose();};},[provided]);
  return port?<TerminalSurface threadId={threadId} port={port}/>:<p>Opening terminal view…</p>;
}
