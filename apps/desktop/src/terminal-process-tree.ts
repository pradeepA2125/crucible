import { execFile } from "node:child_process";
import { promisify } from "node:util";
export interface ProcessIdentity {pid:number;parent:number;group:number;tty:string;started:string;}
export interface OwnedTree { refresh():Promise<void>; terminate():Promise<void>; }
const exec=promisify(execFile);
let reading:Promise<ProcessIdentity[]>|undefined;
export function parseProcesses(text:string):ProcessIdentity[]{return text.split("\n").flatMap(line=>{const p=line.trim().split(/\s+/);return p.length===9&&p.slice(0,3).every(x=>/^\d+$/.test(x))?[{pid:Number(p[0]),parent:Number(p[1]),group:Number(p[2]),tty:p[3]!,started:p.slice(4).join(" ")}]:[];});}
const snapshot=()=>reading??=(exec("/bin/ps",["-axo","pid=,ppid=,pgid=,tty=,lstart="],{timeout:1000,maxBuffer:4*1024*1024}).then(r=>parseProcesses(r.stdout)).finally(()=>{reading=undefined;}));
const same=(a:ProcessIdentity,b:ProcessIdentity)=>a.pid===b.pid&&a.started===b.started;
/** Captured PPID descendants, never a guessed/reused PID or a foreign process group.
 * Keep the shell alive while terminating jobs so job-control groups remain attributable.
 * Detached daemons that leave this tree are outside terminal lifetime semantics.
 */
export async function createOwnedTree(pid:number,parent:number,read:()=>Promise<ProcessIdentity[]>=snapshot,signal:(pid:number,s:NodeJS.Signals)=>void=(p,s)=>process.kill(p,s),privateGroup=false):Promise<OwnedTree>{
 const first=(await read()).find(p=>p.pid===pid);if(!first || first.parent!==parent || !first.started)throw new Error("Cannot verify the shell process identity. Session cleanup remains pending.");
 if(privateGroup && first.group!==pid)throw new Error("Private worker is not its own process-group leader.");
 const known=new Map<number,ProcessIdentity>([[pid,first]]);let terminating:Promise<void>|undefined;
 const collect=(rows:ProcessIdentity[])=>{const by=new Map(rows.map(p=>[p.pid,p]));const owned=new Map<number,ProcessIdentity>();for(const [id,identity]of known){const live=by.get(id);if(live&&same(identity,live))owned.set(id,live);}
  // A detached supervisor stays live while collecting its private group, so its
  // group ID cannot be reused. Ordinary terminal callers retain PPID-only behavior.
  if(privateGroup && owned.has(pid))for(const p of rows)if(p.group===first.group){known.set(p.pid,p);owned.set(p.pid,p);}
  let added=true;while(added){added=false;for(const p of rows)if(!owned.has(p.pid)&&owned.has(p.parent)){known.set(p.pid,p);owned.set(p.pid,p);added=true;}}
  return owned;
 };
 const refresh=async()=>{collect(await read());};
 const kill=async(targets:ProcessIdentity[],sig:NodeJS.Signals)=>{const current=new Map((await read()).map(p=>[p.pid,p]));for(const target of targets){const live=current.get(target.pid);if(!live||!same(target,live))continue;try{signal(target.pid,sig);}catch(e){if((e as NodeJS.ErrnoException).code!=="ESRCH")throw e;}}};
 const run=async()=>{let rootSignalled=false;const deadline=Date.now()+2200;let emptyChildren=0;
  while(Date.now()<deadline){const owned=collect(await read()),children=[...owned.values()].filter(p=>p.pid!==pid),root=owned.get(pid);
   if(!owned.size)return;
   if(children.length){emptyChildren=0;await kill(children,rootSignalled?"SIGKILL":"SIGTERM");await new Promise(r=>setTimeout(r,60));await kill([...collect(await read()).values()].filter(p=>p.pid!==pid),"SIGKILL");}
   else emptyChildren++;
   if(root && (emptyChildren>=2||!children.length&&rootSignalled)){await kill([root],rootSignalled?"SIGKILL":"SIGTERM");rootSignalled=true;}
   // Known descendants stay attributable after root exit/reparenting. Never use
   // macOS's hidden sess field as a numeric session ID, nor kill foreign groups.
   await new Promise(r=>setTimeout(r,40));
  }
  if(collect(await read()).size)throw new Error("Owned terminal descendants have not exited. Session slot is retained; retry End session.");
 };
 return {refresh,terminate:()=>terminating??=(run().finally(()=>{terminating=undefined;}))};
}
