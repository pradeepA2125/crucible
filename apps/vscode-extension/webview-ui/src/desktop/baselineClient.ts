import { createRestoreClient,type RestorePort } from "./restoreClient";
import { z } from "zod";
import { baselineIdentity, baselinePath, baselineRequest, parseBaselineReply, type BaselineOperation } from "../../../../desktop/src/baseline-protocol";
import type { CheckpointIdentity, BaselineScope, BaselineDiff } from "../../../../desktop/src/workspace-baseline";
export interface BaselineIdentity {viewId:string;projectId:string|null;threadId:string|null;}
export type BaselineList = {state:"unavailable";reason:string}|{state:"ready";checkpoints:CheckpointIdentity[];omitted:number};
export class BaselineInspectionError extends Error {constructor(readonly reason:string,message:string){super(message);}}
export interface BaselinePort {
  restore?:RestorePort;
  list(refresh?:boolean):Promise<BaselineList>;scope(checkpoint:CheckpointIdentity):Promise<BaselineScope>;diff(generation:string,path:string):Promise<BaselineDiff>;
  context():BaselineIdentity|null;onContextChange(callback:(identity:BaselineIdentity|null)=>void):()=>void;dispose():void;
}
type Result=BaselineList|BaselineScope|BaselineDiff;
type Job={id:string;operation:BaselineOperation;identity:BaselineIdentity;checkpoint?:CheckpointIdentity;generation?:string;path?:string;
  promise:Promise<Result>;resolve(value:Result):void;reject(error:Error):void;expired:boolean;timer?:ReturnType<typeof setTimeout>;recovery?:ReturnType<typeof setTimeout>;};
const id=z.string().min(1).max(1024);
const contextSchema=z.object({type:z.literal("desktop/baselineContext"),viewId:id,projectId:id.nullable(),threadId:id.nullable()}).strict();
const same=(a:BaselineIdentity|null,b:BaselineIdentity|null)=>a?.viewId===b?.viewId&&a?.projectId===b?.projectId&&a?.threadId===b?.threadId;
const sameCheckpoint=(a:CheckpointIdentity|undefined,b:CheckpointIdentity)=>a?.seq===b.seq&&a.anchorMessageId===b.anchorMessageId&&a.createdAt===b.createdAt;
/** Read-only, thread-bound counterpart of the host broker. Created before webviewReady. */
export function createBaselineClient(send:(message:unknown)=>void,target:Window=window):BaselinePort {
  const active=new Map<"metadata"|"diff",Job>(),generations=new Map<string,Set<string>>(),listeners=new Set<(identity:BaselineIdentity|null)=>void>();
  let identity:BaselineIdentity|null=null,cachedList:BaselineList|undefined,pendingDiff:Job|undefined,stopped:Error|undefined;
  const restoration=createRestoreClient(send,target,()=>identity);
  const error=(reason:string,message:string)=>new BaselineInspectionError(reason,message);
  const lane=(operation:BaselineOperation)=>operation==="diff"?"diff":"metadata";
  const clear=(job:Job)=>{clearTimeout(job.timer);clearTimeout(job.recovery);};
  const reset=(cause:Error,keepAcknowledgements=false)=>{
    for(const job of active.values()){job.reject(cause);job.expired=true;if(!keepAcknowledgements)clear(job);}
    if(!keepAcknowledgements)active.clear();pendingDiff?.reject(cause);pendingDiff=undefined;generations.clear();cachedList=undefined;
  };
  const fail=(cause:Error)=>{stopped=cause;reset(cause);target.removeEventListener("message",receive);listeners.clear();};
  const unavailable=()=>stopped??(!identity?.projectId?error("project","Wait for the project to connect."):!identity.threadId?error("thread","Start or open a conversation to compare checkpoints."):undefined);
  const start=(job:Job)=>{
    active.set(lane(job.operation),job);
    job.timer=setTimeout(()=>{
      job.expired=true;job.reject(error("timeout","Checkpoint inspection did not respond. Waiting for its last request to finish."));
      job.recovery=setTimeout(()=>fail(error("unavailable","Checkpoint inspection did not reconnect. Reload this view to retry.")),15000);
    },45000);
    try{send(baselineRequest.parse({type:job.operation==="list"?"desktop/baselineList":job.operation==="scope"?"desktop/baselineScope":"desktop/baselineDiff",requestId:job.id,threadId:job.identity.threadId,
      ...(job.operation==="scope"?{checkpoint:job.checkpoint}:job.operation==="diff"?{generation:job.generation,path:job.path}:{})}));}
    catch{fail(error("unavailable","Checkpoint inspection could not connect. Reload this view to retry."));}
  };
  const release=(job:Job)=>{
    clear(job);const slot=lane(job.operation);if(active.get(slot)!==job)return;active.delete(slot);
    if(slot==="diff"&&pendingDiff){const next=pendingDiff;pendingDiff=undefined;
      if(!same(next.identity,identity)||!next.generation||!next.path||!generations.get(next.generation)?.has(next.path))next.reject(error("stale","This comparison is out of date. Refresh."));else start(next);}
  };
  const receive=(event:MessageEvent)=>{
    if(event.source!==target||event.origin!=="app://app")return;
    const raw=event.data;
    if(raw?.type==="desktop/baselineContext"){
      const value=contextSchema.safeParse(raw);if(!value.success)return;
      const next={viewId:value.data.viewId,projectId:value.data.projectId,threadId:value.data.threadId};
      if(!same(identity,next)){reset(error("cancelled","The conversation or project changed. Reopen Changes."),true);identity=next;for(const callback of listeners)callback({...next});}return;
    }
    if(raw?.type!=="desktop/baselineResult"||typeof raw.requestId!=="string")return;
    const job=[...active.values()].find(value=>value.id===raw.requestId);if(!job)return;
    let reply;try{reply=parseBaselineReply(raw,job.operation);}catch{fail(error("invalid","Checkpoint inspection returned an invalid result. Reload this view."));return;}
    // Context changes and expired waits acknowledge host occupancy only, never revive data.
    if(job.expired||!same(job.identity,identity)){release(job);return;}
    if(reply.outcome.status!=="ok"){job.reject(error(reply.outcome.status,reply.outcome.message));release(job);return;}
    if(!same(reply,job.identity)){job.reject(error("cancelled","The conversation or connection changed. Refresh."));release(job);return;}
    if(reply.operation==="list"){
      cachedList=reply.outcome.result;job.resolve(cachedList);
    }else if(reply.operation==="scope"){
      const value=reply.outcome.result;
      if(value.state==="ready"){
        if(!job.checkpoint||!sameCheckpoint(job.checkpoint,value.checkpoint)){fail(error("invalid","Checkpoint inspection returned another checkpoint. Reload this view."));return;}
        generations.set(value.generation,new Set(value.changes.map(entry=>entry.path)));while(generations.size>2)generations.delete(generations.keys().next().value!);
      }job.resolve(value);
    }else{
      if(reply.generation!==job.generation||reply.path!==job.path){fail(error("invalid","Checkpoint inspection returned another file. Reload this view."));return;}
      if(!job.generation||!job.path||!generations.get(job.generation)?.has(job.path))job.reject(error("stale","This comparison is out of date. Refresh."));else job.resolve(reply.outcome.result);
    }
    release(job);
  };
  const make=(operation:BaselineOperation,extra:Pick<Job,"checkpoint"|"generation"|"path">={}):Job=>{
    let resolve!:Job["resolve"],reject!:Job["reject"];const promise=new Promise<Result>((done,refused)=>{resolve=done;reject=refused;});
    return{id:crypto.randomUUID(),operation,identity:{...identity!},promise,resolve,reject,expired:false,...extra};
  };
  target.addEventListener("message",receive);
  return {
    restore:restoration.run,
    context:()=>identity?{...identity}:null,onContextChange:callback=>{if(!stopped)listeners.add(callback);return()=>{listeners.delete(callback);};},
    list:(refresh=false)=>{
      const cause=unavailable();if(cause)return Promise.reject(cause);if(refresh)cachedList=undefined;
      if(cachedList)return Promise.resolve(cachedList);
      const running=active.get("metadata");if(running){if(running.operation==="list"&&!running.expired)return running.promise as Promise<BaselineList>;return Promise.reject(error("busy","A checkpoint inspection is still finishing. Retry shortly."));}
      const job=make("list");start(job);return job.promise as Promise<BaselineList>;
    },
    scope:chosen=>{
      const cause=unavailable();if(cause)return Promise.reject(cause);
      const parsed=baselineIdentity.safeParse(chosen);if(!parsed.success)return Promise.reject(error("unsupported","Choose a retained checkpoint."));
      const running=active.get("metadata");if(running){if(running.operation==="scope"&&!running.expired&&sameCheckpoint(running.checkpoint,parsed.data))return running.promise as Promise<BaselineScope>;return Promise.reject(error("busy","A checkpoint inspection is still finishing. Retry shortly."));}
      const job=make("scope",{checkpoint:parsed.data});start(job);return job.promise as Promise<BaselineScope>;
    },
    diff:(generation,path)=>{
      const cause=unavailable();if(cause)return Promise.reject(cause);
      if(!baselinePath.safeParse(path).success)return Promise.reject(error("unsupported","This filename cannot be inspected."));
      if(!generations.get(generation)?.has(path))return Promise.reject(error("stale","This comparison is out of date. Refresh."));
      const running=active.get("diff");
      if(running&&!running.expired&&running.generation===generation&&running.path===path){pendingDiff?.reject(error("superseded","A newer file selection replaced this inspection."));pendingDiff=undefined;return running.promise as Promise<BaselineDiff>;}
      if(pendingDiff?.generation===generation&&pendingDiff.path===path)return pendingDiff.promise as Promise<BaselineDiff>;
      pendingDiff?.reject(error("superseded","A newer file selection replaced this inspection."));
      const job=make("diff",{generation,path});if(running)pendingDiff=job;else start(job);return job.promise as Promise<BaselineDiff>;
    },
    dispose:()=>{restoration.dispose();fail(error("cancelled","Checkpoint inspection cancelled."));},
  };
}
