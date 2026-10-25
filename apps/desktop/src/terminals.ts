import { createOwnedTree, type OwnedTree } from "./terminal-process-tree.js";
import { randomUUID } from "node:crypto";
import { TERMINAL_BYTES, TERMINAL_CAP, TERMINAL_CHUNK, type TerminalInfo } from "./terminal-protocol.js";
export interface TerminalWorker { readonly pid?:number|undefined; postMessage(message: unknown): void; kill(): boolean | void; on(event: "message", listener: (message: any) => void): unknown; on(event: "exit", listener: (code: number) => void): unknown; }
export interface ShellLease { current(): boolean; }
interface Session { info: TerminalInfo; tree?:OwnedTree; tracking?:ReturnType<typeof setInterval>; workerExited?:boolean; worker: TerminalWorker; lease: ShellLease; bytes: Buffer; pending: number; lastFrame: number; settled: boolean; done: Promise<void>; finish(): void; start: Promise<void>; ready(error?: Error): void; }
/** A host-local PTY registry. Never stores agent sessions or provider credentials. */
export class Terminals {
  private sessions = new Map<string, Session>();
  private disposed = false;
  constructor(private spawn: () => TerminalWorker, private platform = process.platform, private environment: NodeJS.ProcessEnv = process.env) {}
  busy(projectId: string): boolean { return [...this.sessions.values()].some(s => s.info.projectId === projectId && !s.settled); }
  list(projectId: string): TerminalInfo[] { return [...this.sessions.values()].filter(s => s.info.projectId === projectId).map(s => ({...s.info})); }
  private get(projectId: string, id: string): Session { const s=this.sessions.get(id); if(!s || s.info.projectId!==projectId)throw new Error("This shell is unavailable in the selected project.");return s; }
  async start(projectId: string, root: string, lease: ShellLease, cols = 100, rows = 30): Promise<TerminalInfo> {
    if(this.disposed || !lease.current())throw new Error("The controlling project changed. Reopen its terminal.");
    if(this.sessions.size>=TERMINAL_CAP || this.list(projectId).length>=4)throw new Error("Shell session limit reached (4 per project, 16 total). End running shells and remove exited entries first.");
    const id=randomUUID(),worker=this.spawn(),shell=this.platform==="win32" ? `${this.environment.SystemRoot ?? "C:\\Windows"}\\System32\\cmd.exe` : this.platform==="darwin" ? "/bin/zsh" : "/bin/bash";
    let resolve!:()=>void,reject!:(e:Error)=>void,finish!:()=>void;
    const start=new Promise<void>((ok,no)=>{resolve=ok;reject=no;}),done=new Promise<void>(ok=>{finish=ok;});
    const s:Session={info:{id,projectId,provenance:"user",initialCwd:root,shell,status:"starting",exitCode:null,hostAlive:true,start:0,end:0,cols,rows},worker,lease,bytes:Buffer.alloc(0),pending:0,lastFrame:0,settled:false,done,finish,start,ready:e=>e?reject(e):resolve()};
    this.sessions.set(id,s);
    worker.on("message",raw=>this.message(s,raw));
    worker.on("exit",code=>{s.workerExited=true;clearInterval(s.tracking);if(["starting","running","stopping"].includes(s.info.status)){s.info.status="error";s.info.error=`Terminal host exited (${code}). The shell was not restarted.`;s.ready(new Error(s.info.error));}void(async()=>{try{await s.tree?.terminate();s.settled=true;s.info.hostAlive=false;if(["starting","running","stopping"].includes(s.info.status)){s.info.status="error";s.info.error=`Terminal host exited (${code}). The shell was not restarted.`;}s.ready(s.info.status==="error"?new Error(s.info.error):undefined);s.finish();}catch(e){s.info.status="error";s.info.error=(e as Error).message;s.ready(e as Error);}})();});
    const timer=setTimeout(()=>{s.info.status="error";s.info.error="Terminal launch timed out. The host was stopped.";worker.kill();s.ready(new Error(s.info.error));},8000);
    try { worker.postMessage({type:"start",id,cwd:root,shell,args:this.platform==="win32"?[]:this.platform==="darwin"?["-f","-i"]:["--noprofile","--norc","-i"],cols,rows});await start;if(!lease.current()){await this.end(projectId,id);throw new Error("Project ownership changed during shell launch.");}return {...s.info}; }
    finally {clearTimeout(timer);}
  }
  private message(s:Session,m:any):void {
    if(!m || m.id!==s.info.id || s.settled)return;
    if(m.type==="ready" && s.info.status==="starting" && Number.isSafeInteger(m.pid) && m.pid>0){s.info.pid=m.pid;
      void(async()=>{try{if(this.platform!=="win32"&&s.worker.pid)s.tree=await createOwnedTree(m.pid,s.worker.pid);if(s.workerExited)return;s.info.status="running";s.tracking=setInterval(()=>{void s.tree?.refresh().catch(()=>{});},1000);s.ready();}catch(e){s.info.status="error";s.info.error=(e as Error).message;s.worker.kill();s.ready(e as Error);}})();}
    else if(m.type==="data" && typeof m.data==="string" && m.data.length<=45000 && Number.isSafeInteger(m.seq)){
      const data=Buffer.from(m.data,"base64");if(data.length>TERMINAL_CHUNK || m.seq!==s.lastFrame+1 || data.toString("base64")!==m.data){s.info.status="error";s.info.error="Terminal output transport was invalid. Session stopped.";s.worker.kill();return;}s.lastFrame=m.seq;
      s.bytes=Buffer.concat([s.bytes,data]);s.info.end+=data.length;if(s.bytes.length>TERMINAL_BYTES)s.bytes=s.bytes.subarray(s.bytes.length-TERMINAL_BYTES);s.info.start=s.info.end-s.bytes.length;
      s.worker.postMessage({type:"ack",id:s.info.id,seq:m.seq});
    } else if(m.type==="inputAck") {s.pending=Math.max(0,s.pending-1);}
    else if(m.type==="exited" && Number.isSafeInteger(m.exitCode)){s.info.status="exited";s.info.exitSignal=typeof m.signal==="number"?m.signal:0;s.info.exitCode=s.info.exitSignal?null:m.exitCode;}
    else if(m.type==="error"){s.info.status="error";s.info.error=String(m.message).slice(0,2048);s.ready(new Error(s.info.error));}
  }
  read(projectId:string,id:string,offset:number){const s=this.get(projectId,id),reset=offset<s.info.start||offset>s.info.end,at=reset?s.info.start:offset;const data=s.bytes.subarray(at-s.info.start,at-s.info.start+65536);return {session:{...s.info},data:data.toString("base64"),next:at+data.length,reset};}
  input(projectId:string,id:string,input:string):void{const s=this.live(projectId,id);if(Buffer.byteLength(input)>4096 || s.pending>=32)throw new Error("Terminal input is busy or too large. Retry smaller input.");s.pending++;s.worker.postMessage({type:"input",id,chars:input});}
  resize(projectId:string,id:string,cols:number,rows:number):void{const s=this.live(projectId,id);s.info.cols=cols;s.info.rows=rows;s.worker.postMessage({type:"resize",id,cols,rows});}
  private live(projectId:string,id:string):Session{const s=this.get(projectId,id);if(s.info.status!=="running" || !s.lease.current())throw new Error("This shell is exited or its controlling project changed. No input was sent.");return s;}
  async end(projectId:string,id:string):Promise<void>{const s=this.get(projectId,id);if(s.settled)return;s.info.status="stopping";if(s.workerExited){await s.tree?.terminate();s.settled=true;s.info.hostAlive=false;s.info.status="error";s.info.error??="Terminal host exited; its owned jobs are stopped.";s.finish();return;}await s.tree?.refresh();s.worker.postMessage({type:"end",id});const timer=setTimeout(()=>s.worker.kill(),2500);let deadline:ReturnType<typeof setTimeout>|undefined;try{await Promise.race([s.done,new Promise<never>((_,reject)=>{deadline=setTimeout(()=>reject(new Error("Terminal host did not exit. Its slot is retained; retry Quit after checking the shell.")),5500);})]);}finally{clearTimeout(timer);clearTimeout(deadline);}}
  forget(projectId:string,id:string):void{const s=this.get(projectId,id);if(!s.settled)throw new Error("End this shell before removing its output.");this.sessions.delete(id);}
  async dispose():Promise<void>{this.disposed=true;const results=await Promise.allSettled([...this.sessions.values()].map(s=>this.end(s.info.projectId,s.info.id)));const failed=results.find(r=>r.status==="rejected");if(failed?.status==="rejected")throw failed.reason;this.sessions.clear();}
}
/** Native user environment only. No backend/project credentials, injection flags or venv. */
export function shellEnvironment(source:NodeJS.ProcessEnv):NodeJS.ProcessEnv{const env:NodeJS.ProcessEnv={TERM:"xterm-256color",COLORTERM:"truecolor"};for(const k of ["PATH","HOME","USER","LOGNAME","LANG","LC_ALL","LC_CTYPE","TMPDIR","TEMP","TMP","SystemRoot","WINDIR","USERPROFILE","APPDATA","LOCALAPPDATA","HOMEDRIVE","HOMEPATH"]){if(source[k])env[k]=source[k];}return env;}
