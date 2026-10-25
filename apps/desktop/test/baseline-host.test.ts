import { expect, it, vi } from "vitest";
import { randomUUID } from "node:crypto";
import { currentBaselineBackend, syncBaselineThread } from "../src/baseline-host.js";
import { createWorkspaceBaseline } from "../src/workspace-baseline.js";
import { createWorkspaceBaselineRequests } from "../src/workspace-baseline-requests.js";
import { parseEnvelope, parsePayload, type BridgeContext } from "../src/contract.js";
import { refusalMessages } from "../src/refusal.js";
import { parseBaselineReply } from "../src/baseline-protocol.js";
import type { BaselineFile, CheckpointSummary } from "@crucible/editor-client";
const chosen={seq:0,anchorMessageId:"anchor",createdAt:"date"};
const context=():BridgeContext=>({v:1,clientId:"client",viewId:"view",projectId:"project",threadId:"A",processId:"process"});
const summary=():CheckpointSummary=>({...chosen,turnId:"turn",files:[]});
const client=()=>({listCheckpoints:vi.fn(async()=>[summary()]),getCheckpointBaseline:vi.fn(async()=>[{path:"a.ts",fromSeq:0,existed:true,oversize:false}]),
 getCheckpointBaselineFile:vi.fn(async(_thread:string,_seq:number,path:string,_signal?:AbortSignal):Promise<BaselineFile>=>({path,fromSeq:0,state:"captured",content:new TextEncoder().encode("old"),size:3}))});
it("resolves the current gated client for each request and revalidates generations against a restarted backend",async()=>{
 const a=client(),b=client();let current=a;const backend=currentBaselineBackend(()=>current);
 const files={compareBytes:vi.fn(async()=>({path:"a.ts",content:Buffer.from("new"),size:3,truncated:false,mode:0o100644,revision:"revision"}))};
 const reader=createWorkspaceBaseline(files,backend);const result=await reader.scope("A",chosen);expect(result.state).toBe("ready");if(result.state!=="ready")throw Error(result.reason);
 current=b;expect(await reader.diff(result.generation,"a.ts")).toMatchObject({kind:"text"});expect(a.getCheckpointBaselineFile).toHaveBeenCalledTimes(1);expect(b.getCheckpointBaselineFile).toHaveBeenCalledTimes(1);
 b.listCheckpoints.mockResolvedValueOnce([{...summary(),anchorMessageId:"reused",createdAt:"new-date"}]);
 expect(await reader.diff(result.generation,"a.ts")).toMatchObject({kind:"unavailable"});expect(b.getCheckpointBaselineFile).toHaveBeenCalledTimes(1);
 const signal=new AbortController().signal;await backend.getCheckpointBaselineFile!("A",0,"a.ts",signal);expect(b.getCheckpointBaselineFile).toHaveBeenLastCalledWith("A",0,"a.ts",signal);
});
it("older or unavailable backend methods stay unavailable and do not fallback to Git or another thread",async()=>{
 const backend=currentBaselineBackend(()=>({}));const files={compareBytes:vi.fn()};
 const reader=createWorkspaceBaseline(files,backend);expect(await reader.listCheckpoints("A")).toMatchObject({state:"unavailable"});expect(files.compareBytes).not.toHaveBeenCalled();
});
it("actual host thread events cancel live broker reads, while switch intent and same-thread polling do not",async()=>{
 const ctx=context(), post=vi.fn(), backend=client();let finish!:(value:CheckpointSummary[])=>void,entered!:()=>void;
 const started=new Promise<void>(done=>{entered=done;});let signal:AbortSignal|undefined;
 const adapter=currentBaselineBackend(()=>({...backend,listCheckpoints:(_thread:string,abort?:AbortSignal)=>{signal=abort;entered();return new Promise(resolve=>{finish=resolve;});}}));
 const reader=createWorkspaceBaseline({compareBytes:vi.fn()},adapter), broker=createWorkspaceBaselineRequests(reader,()=>({viewId:ctx.viewId,projectId:ctx.projectId,threadId:ctx.threadId}),post);
 const work=broker.handle({type:"desktop/baselineScope",requestId:randomUUID(),threadId:"A",checkpoint:chosen});await started;
 syncBaselineThread(ctx,{type:"switchThread",threadId:"B"},broker.cancel);syncBaselineThread(ctx,{type:"renderThreadList",activeThreadId:"A"},broker.cancel);expect(signal?.aborted).toBe(false);expect(ctx.threadId).toBe("A");
 syncBaselineThread(ctx,{type:"clearThread",threadId:"B"},broker.cancel);expect(signal?.aborted).toBe(true);await work;
 expect(post).toHaveBeenLastCalledWith(expect.objectContaining({threadId:"A",outcome:expect.objectContaining({status:"cancelled"})}));
 finish([summary()]);await new Promise(resolve=>setImmediate(resolve));expect(post).toHaveBeenCalledTimes(1);
 syncBaselineThread(ctx,{type:"renderThreadList",activeThreadId:null},broker.cancel);expect(ctx.threadId).toBeNull();
 await broker.handle({type:"desktop/baselineList",requestId:randomUUID(),threadId:null});expect(post).toHaveBeenLastCalledWith(expect.objectContaining({outcome:{status:"ok",result:expect.objectContaining({state:"unavailable"})}}));
});
it.each(["List","Scope","Diff"] as const)("admits %s only through the existing strict envelope and isolates stale/malformed refusals",operation=>{
 const ctx=context(), request={type:"desktop/baseline"+operation,requestId:randomUUID(),threadId:"A",...(operation==="Scope"?{checkpoint:chosen}:operation==="Diff"?{generation:randomUUID(),path:"a.ts"}:{})};
 expect(parsePayload(request)).toEqual(request);expect(()=>parsePayload({...request,backendURL:"http://evil"})).toThrow();
 expect(()=>parseEnvelope({...ctx,threadId:"B",sequence:1,requestId:"command",payload:request},ctx,0)).toThrow("Stale");
 const replies=refusalMessages(request,"internal validation diagnostic",ctx.projectId,ctx);expect(replies).toHaveLength(1);expect(replies[0]).toMatchObject({type:"desktop/baselineResult",viewId:"view",threadId:"A",outcome:{status:"error"}});
 expect(JSON.stringify(replies)).not.toMatch(/internal validation|setInputEnabled|draftRecovery|appendMessage/);expect(()=>parseBaselineReply(replies[0],operation.toLowerCase() as "list"|"scope"|"diff")).not.toThrow();
 expect(refusalMessages({...request,unexpected:true},"bad schema",ctx.projectId,ctx)).toHaveLength(1);
});
it("ignores uncorrelatable malformed baseline refusals without touching the composer",()=>{
 const ctx=context();expect(refusalMessages({type:"desktop/baselineScope",requestId:"not-uuid"},"bad",ctx.projectId,ctx)).toEqual([]);
 expect(refusalMessages({type:"desktop/baselineDiff",requestId:randomUUID(),generation:"bad",path:"../outside"},"bad",ctx.projectId,ctx)).toEqual([]);
});
