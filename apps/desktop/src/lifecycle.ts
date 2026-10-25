import type { LifecyclePreferences, QuitProject } from "./lifecycle-protocol.js";
export interface Observation extends QuitProject { identity?:string|undefined; }
export interface LifecycleNotice { projectId:string;threadId:string;kind:"attention"|"complete";count:number; }
/** Process-local observed transitions only. Unknown/restart never implies completion. */
export class LifecycleTransitions {
  private previous = new Map<string, Observation>();
  reset():void { this.previous.clear(); }
  update(current:Observation[], preferences:LifecyclePreferences, focused:boolean): {notices:LifecycleNotice[];badge:number;awake:boolean} {
    const notices:LifecycleNotice[]=[],next=new Map<string,Observation>();let badge=0,awake=false;
    for(const item of current){
      const reliable=item.state!=="unknown" && !!item.identity;
      if(reliable){badge+=item.threads.reduce((n,t)=>n+t.gates,0);awake ||= item.owner && item.state==="busy";}
      awake ||= item.shells.length>0;
      const before=this.previous.get(item.id);
      if(reliable && before && before.identity===item.identity && before.state!=="unknown" && !focused && preferences.notifications!=="off"){
        const waiting=item.threads.filter(t=>t.gates>0 && t.gate_ids.some(id=>!before.threads.find(b=>b.thread_id===t.thread_id)?.gate_ids.includes(id)));
        if(waiting.length)notices.push({projectId:item.id,threadId:waiting[0]!.thread_id,kind:"attention",count:waiting.reduce((n,t)=>n+t.gates,0)});
        else if(preferences.notifications==="all"){
          const done=item.threads.filter(t=>!t.turn_active && !t.agents.length && !t.teams.length && !t.sessions.length && !t.gates &&
            before.threads.some(b=>b.thread_id===t.thread_id && (b.turn_active||b.agents.length||b.teams.length||b.sessions.length)));
          if(done.length)notices.push({projectId:item.id,threadId:done[0]!.thread_id,kind:"complete",count:done.length});
        }
      }
      if(reliable)next.set(item.id,item);
    }
    this.previous=next;
    return {notices,badge:Math.min(badge,999),awake:preferences.keepAwake&&awake};
  }
}
/** Only identity/resource changes invalidate approval; output and timestamps do not. */
export function quitFingerprint(projects:Observation[]):string {
  return JSON.stringify(projects.map(p=>({id:p.id,state:p.state,owner:p.owner,identity:p.identity,
    shells:p.shells.map(s=>s.id).sort(),threads:p.threads.filter(t=>t.turn_active||t.gates||t.agents.length||t.teams.length||t.sessions.length)
      .map(t=>({id:t.thread_id,turn:t.turn_active,gates:[...t.gate_ids].sort(),agents:t.agents.map(a=>a.id).sort(),teams:t.teams.map(a=>a.id).sort(),sessions:t.sessions.map(a=>a.id).sort()})).sort((a,b)=>a.id.localeCompare(b.id))})).sort((a,b)=>a.id.localeCompare(b.id)));
}
export async function cleanAll(actions:Array<()=>void|Promise<void>>):Promise<void>{
  const results=await Promise.allSettled(actions.map(action=>Promise.resolve().then(action)));
  const errors=results.flatMap(r=>r.status==="rejected"?[r.reason]:[]);
  if(errors.length)throw new AggregateError(errors,"Some owned resources could not be stopped. Retry quitting.");
}
