import { restoreRequest,restoreReply,type RestoreAction,type RestoreResult } from "../../../../desktop/src/restore-protocol";
import type { BaselineIdentity } from "./baselineClient";
export type RestorePort=(action:RestoreAction)=>Promise<RestoreResult>;
const same=(a:BaselineIdentity|null,b:BaselineIdentity|null)=>!!a&&!!b&&a.viewId===b.viewId&&a.projectId===b.projectId&&a.threadId===b.threadId;
/** One physical mutation lane. Ambiguous completion never automatically retries. */
export function createRestoreClient(send:(message:unknown)=>void,target:Window,context:()=>BaselineIdentity|null){
 let active:{id:string;identity:BaselineIdentity;resolve:(v:RestoreResult)=>void;reject:(e:Error)=>void;timer:ReturnType<typeof setTimeout>}|undefined,stopped=false;
 const receive=(event:MessageEvent)=>{
  if(event.source!==target||event.origin!=="app://app"||event.data?.type!=="desktop/restoreResult"||event.data.requestId!==active?.id)return;
  const job=active!;clearTimeout(job.timer);active=undefined;
  const parsed=restoreReply.safeParse(event.data);
  if(!parsed.success){job.reject(new Error("File recovery returned an invalid reply. Check recovery history."));return;}
  const reply=parsed.data;
  if(!same(job.identity,context())||!same(reply,job.identity)){job.reject(new Error("The conversation changed. Check its recovery history before retrying."));return;}
  if(reply.error)job.reject(new Error(reply.error));else job.resolve(reply.result!);
 };
 target.addEventListener("message",receive);
 const run:RestorePort=action=>{
  const identity=context();if(stopped||!identity?.projectId||!identity.threadId)return Promise.reject(new Error("Open a project conversation to use file recovery."));
  if(active)return Promise.reject(new Error("File recovery is still finishing. Check its result before another action."));
  return new Promise((resolve,reject)=>{
   const id=crypto.randomUUID();const job={id,identity:{...identity},resolve,reject,timer:setTimeout(()=>{reject(new Error("File recovery has not confirmed completion. Reopen Changes and check recovery history before retrying."));},45000)};active=job;
   try{send(restoreRequest.parse({type:"desktop/restore",requestId:id,threadId:identity.threadId,action}));}catch{clearTimeout(job.timer);active=undefined;reject(new Error("File recovery could not connect."));}
  });
 };
 return {run,dispose:()=>{stopped=true;target.removeEventListener("message",receive);if(active){clearTimeout(active.timer);active.reject(new Error("File recovery view closed. Check history for a pending operation."));active=undefined;}}};
}
