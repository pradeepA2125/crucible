import { afterEach, expect, it, vi } from "vitest";
import { randomUUID } from "node:crypto";
import { createWorkspaceBaselineRequests, type BaselineContext } from "../src/workspace-baseline-requests.js";
import { BASELINE_REPLY_BYTES, baselineRequest, parseBaselineReply, type BaselineReply, type BaselineRequest } from "../src/baseline-protocol.js";
import { BaselineReadStopped } from "../src/baseline-read-operation.js";
import type { BaselineScope, BaselineDiff } from "../src/workspace-baseline.js";
const selected = {seq:0,anchorMessageId:"anchor",createdAt:"date"};
const generation = randomUUID();
const scope = (): Extract<BaselineScope,{state:"ready"}> => ({state:"ready",generation,checkpoint:{...selected},changes:[{path:"a.ts",status:"M",fromSeq:0,supplying:{...selected}}],unchanged:0,createdRemoved:0,uncompared:[],omitted:0,partial:false});
const deferred = <T,>() => { let resolve!:(value:T)=>void; const promise=new Promise<T>(done=>{resolve=done;});return {resolve,promise};};
const setup = () => {
  const context: BaselineContext = {viewId:"view",projectId:"project",threadId:"A"};
  const post=vi.fn<(reply:BaselineReply)=>void>();
  const reader={listCheckpoints:vi.fn(async()=>({state:"ready" as const,checkpoints:[{...selected,turnId:"turn",files:[{path:"private-baseline-path",existed:true,oversize:false}]}]})),
    scope:vi.fn(async(_thread:string,_chosen:typeof selected,_options?:{signal?:AbortSignal}):Promise<BaselineScope>=>scope()),
    diff:vi.fn(async(_generation:string,_path:string,_options?:{signal?:AbortSignal}):Promise<BaselineDiff>=>({kind:"binary",fingerprint:"a".repeat(64)}))};
  const broker=createWorkspaceBaselineRequests(reader,()=>context,post);
  const request=(operation:"List"|"Scope"|"Diff",extra:Record<string,unknown>={}):BaselineRequest => baselineRequest.parse({type:"desktop/baseline"+operation,requestId:randomUUID(),threadId:context.threadId,...(operation==="Scope"?{checkpoint:selected}:operation==="Diff"?{generation,path:"a.ts"}:{}),...extra});
  return {context,post,reader,broker,request};
};
afterEach(()=>vi.useRealTimers());
it("is lazy and strips file paths and unrelated checkpoint metadata out of picker replies",async()=>{
 const f=setup();expect(f.reader.listCheckpoints).not.toHaveBeenCalled();
 f.reader.listCheckpoints.mockResolvedValueOnce({state:"ready",checkpoints:Array.from({length:201},(_,seq)=>({...selected,seq,turnId:"turn",files:[{path:"private-baseline-path",existed:true,oversize:false}]}))});
 await f.broker.handle(f.request("List"));const reply=f.post.mock.calls[0]![0];expect(reply).toMatchObject({operation:"list",viewId:"view",threadId:"A",outcome:{status:"ok",result:{state:"ready",omitted:1}}});
 if(reply.operation==="list"&&reply.outcome.status==="ok"&&reply.outcome.result.state==="ready") {
   expect(reply.outcome.result.checkpoints).toHaveLength(200);expect(reply.outcome.result.checkpoints[0]!.seq).toBe(200);
 }
 expect(JSON.stringify(reply)).not.toMatch(/private-baseline-path|turnId|files/);
});
it("shares one metadata lane, permits a parallel diff, and coalescing remains a renderer responsibility",async()=>{
 const f=setup();await f.broker.handle(f.request("Scope"));f.post.mockClear();const pending=deferred<BaselineScope>();f.reader.scope.mockImplementationOnce(()=>pending.promise);
 const scan=f.broker.handle(f.request("Scope"));await Promise.resolve();await f.broker.handle(f.request("List"));await f.broker.handle(f.request("Diff"));
 expect(f.reader.listCheckpoints).not.toHaveBeenCalled();expect(f.reader.diff).toHaveBeenCalledTimes(1);expect(f.post.mock.calls.map(([reply])=>reply.outcome.status)).toEqual(["busy","ok"]);
 pending.resolve(scope());await scan;expect(f.post).toHaveBeenCalledTimes(3);
});
it("rejects thread A generation in B, unknown generation and nonmember paths before any reader I/O",async()=>{
 const f=setup();await f.broker.handle(f.request("Scope"));f.context.threadId="B";
 await f.broker.handle(f.request("Diff"));expect(f.reader.diff).not.toHaveBeenCalled();expect(f.post).toHaveBeenLastCalledWith(expect.objectContaining({threadId:"B",outcome:{status:"ok",result:{kind:"unavailable",reason:expect.stringContaining("out of date")}}}));
 f.context.threadId="A";await f.broker.handle(f.request("Diff",{path:"outside.ts"}));await f.broker.handle(f.request("Diff",{generation:randomUUID()}));expect(f.reader.diff).not.toHaveBeenCalled();
 await f.broker.handle(f.request("Diff"));expect(f.reader.diff).toHaveBeenCalledTimes(1);
});
it("checks explicit request thread and handles new chat without empty-thread HTTP",async()=>{
 const f=setup();await f.broker.handle(f.request("List",{threadId:"B"}));expect(f.post).toHaveBeenLastCalledWith(expect.objectContaining({outcome:expect.objectContaining({status:"cancelled"})}));
 f.context.threadId=null;for(const operation of ["List","Scope","Diff"] as const)await f.broker.handle(f.request(operation));
 expect(f.reader.listCheckpoints).not.toHaveBeenCalled();expect(f.reader.scope).not.toHaveBeenCalled();expect(f.reader.diff).not.toHaveBeenCalled();
 expect(f.post.mock.calls.slice(1).every(([reply])=>reply.outcome.status==="ok"&&JSON.stringify(reply).includes("Start or open a conversation"))).toBe(true);
});
it("captures identity by value and sends old-thread cancellation acknowledgement without old data",async()=>{
 const f=setup(), pending=deferred<BaselineScope>();f.reader.scope.mockImplementationOnce(()=>pending.promise);
 const work=f.broker.handle(f.request("Scope"));await Promise.resolve();f.context.threadId="B";pending.resolve(scope());await work;
 expect(f.post).toHaveBeenCalledTimes(1);expect(f.post).toHaveBeenLastCalledWith(expect.objectContaining({threadId:"A",outcome:expect.objectContaining({status:"cancelled"})}));
 await f.broker.handle(f.request("Diff"));expect(f.reader.diff).not.toHaveBeenCalled();
});
it("cancels both lanes immediately, clears generation authority and never publishes late ignored-abort results",async()=>{
 const f=setup();await f.broker.handle(f.request("Scope"));f.post.mockClear();const pending=deferred<BaselineScope>(), detail=deferred<BaselineDiff>();
 f.reader.scope.mockImplementationOnce(()=>pending.promise);f.reader.diff.mockImplementationOnce(()=>detail.promise);
 const a=f.broker.handle(f.request("Scope")),b=f.broker.handle(f.request("Diff"));await Promise.resolve();
 const signals=[f.reader.scope.mock.calls[1]![2]!.signal,f.reader.diff.mock.calls[0]![2]!.signal];f.broker.cancel();f.context.threadId="B";await Promise.all([a,b]);expect(signals.every(signal=>signal?.aborted)).toBe(true);
 expect(f.post.mock.calls.every(([reply])=>reply.threadId==="A"&&reply.outcome.status==="cancelled")).toBe(true);
 pending.resolve(scope());detail.resolve({kind:"too-large"});await new Promise(resolve=>setImmediate(resolve));expect(f.post).toHaveBeenCalledTimes(2);
 f.context.threadId="A";await f.broker.handle(f.request("Diff"));expect(f.reader.diff).toHaveBeenCalledTimes(1);
});
it("suppresses stale view and disposed replies and lets a fresh view read",async()=>{
 const f=setup(),pending=deferred<BaselineScope>();f.reader.scope.mockImplementationOnce(()=>pending.promise);
 const work=f.broker.handle(f.request("Scope"));await Promise.resolve();f.broker.cancel();f.context.viewId="next";await work;pending.resolve(scope());expect(f.post).not.toHaveBeenCalled();
 await f.broker.handle(f.request("Scope"));expect(f.post).toHaveBeenCalledTimes(1);
 f.reader.scope.mockImplementationOnce(()=>new Promise(()=>{}));const final=f.broker.handle(f.request("Scope"));await Promise.resolve();f.broker.dispose();await final;await f.broker.handle(f.request("List"));expect(f.post).toHaveBeenCalledTimes(1);
});
it("uses independent 30s metadata and 15s diff deadlines, recovers logical slots, consumes late settlement",async()=>{
 vi.useFakeTimers();const f=setup();await f.broker.handle(f.request("Scope"));f.post.mockClear();const pending=deferred<BaselineScope>(),detail=deferred<BaselineDiff>();
 f.reader.scope.mockImplementationOnce(()=>pending.promise);f.reader.diff.mockImplementationOnce(()=>detail.promise);
 const a=f.broker.handle(f.request("Scope")),b=f.broker.handle(f.request("Diff"));await Promise.resolve();await vi.advanceTimersByTimeAsync(15001);await b;
 expect(f.post).toHaveBeenCalledTimes(1);expect(f.post.mock.calls[0]![0]).toMatchObject({operation:"diff",outcome:{status:"timeout"}});
 await f.broker.handle(f.request("Diff"));await vi.advanceTimersByTimeAsync(15000);await a;expect(f.post.mock.calls.at(-1)![0]).toMatchObject({operation:"scope",outcome:{status:"timeout"}});
 await f.broker.handle(f.request("List"));pending.resolve(scope());detail.resolve({kind:"too-large"});await vi.advanceTimersByTimeAsync(0);expect(f.post).toHaveBeenCalledTimes(4);
});
it("preserves reader typed outcomes and hides unexpected diagnostics",async()=>{
 const f=setup();for(const reason of ["busy","timeout","cancelled"] as const){f.reader.scope.mockRejectedValueOnce(new BaselineReadStopped(reason));await f.broker.handle(f.request("Scope"));expect(f.post.mock.calls.at(-1)![0].outcome.status).toBe(reason);}
 f.reader.scope.mockRejectedValueOnce(Error("secret diagnostic"));await f.broker.handle(f.request("Scope"));expect(JSON.stringify(f.post.mock.calls.at(-1))).not.toContain("secret");expect(f.post.mock.calls.at(-1)![0].outcome.status).toBe("error");
});
it("keeps only two generations, and malformed scopes never grant diff authority",async()=>{
 const f=setup();for(let n=0;n<3;n++){f.reader.scope.mockResolvedValueOnce({...scope(),generation:n?randomUUID():generation});await f.broker.handle(f.request("Scope"));}
 await f.broker.handle(f.request("Diff"));expect(f.reader.diff).not.toHaveBeenCalled();
 f.reader.scope.mockResolvedValueOnce({...scope(),changes:[{path:"a.ts",status:"M",fromSeq:1,supplying:selected}]} as BaselineScope);
 await f.broker.handle(f.request("Scope"));expect(f.post.mock.calls.at(-1)![0].outcome.status).toBe("error");await f.broker.handle(f.request("Diff"));expect(f.reader.diff).not.toHaveBeenCalled();
});
it("maps UTF8 and JSON-escaping envelope overhead to typed too-large without granting authority",async()=>{
 const f=setup();f.reader.scope.mockResolvedValueOnce({...scope(),checkpoint:{...selected,anchorMessageId:'"'.repeat(BASELINE_REPLY_BYTES/2)}} as BaselineScope);
 await f.broker.handle(f.request("Scope"));expect(f.post.mock.calls.at(-1)![0].outcome.status).toBe("too-large");await f.broker.handle(f.request("Diff"));expect(f.reader.diff).not.toHaveBeenCalled();
 f.reader.scope.mockResolvedValueOnce({...scope(),checkpoint:{...selected,anchorMessageId:'界'.repeat(BASELINE_REPLY_BYTES/3)}} as BaselineScope);
 await f.broker.handle(f.request("Scope"));expect(f.post.mock.calls.at(-1)![0].outcome.status).toBe("too-large");
});
it("rejects hostile requests and inconsistent result facts at the strict browser boundary",()=>{
 const f=setup();expect(()=>baselineRequest.parse({...f.request("Diff"),path:"../outside"})).toThrow();expect(()=>baselineRequest.parse({...f.request("List"),backendURL:"http://evil"})).toThrow();
 const reply={type:"desktop/baselineResult",requestId:randomUUID(),...f.context,operation:"scope",outcome:{status:"ok",result:scope()}};
 expect(()=>parseBaselineReply(reply,"scope")).not.toThrow();
 expect(()=>parseBaselineReply({...reply,outcome:{status:"ok",result:{...scope(),unchanged:500}}},"scope")).toThrow();
 expect(()=>parseBaselineReply({...reply,outcome:{status:"ok",result:{...scope(),partial:true}}},"scope")).toThrow();
 expect(()=>parseBaselineReply({...reply,outcome:{status:"ok",result:{...scope(),checkpoint:{...selected,seq:1}}}},"scope")).toThrow();
 const diff={type:"desktop/baselineResult",requestId:randomUUID(),...f.context,operation:"diff",generation,path:"a.ts",outcome:{status:"ok",result:{kind:"text",unified:"x\n".repeat(3000),additions:0,deletions:0,truncated:false,fingerprint:"a".repeat(64)}}};
 expect(()=>parseBaselineReply(diff,"diff")).toThrow();
});

it("rejects a well-formed scope for the wrong requested checkpoint before publishing or granting authority",async()=>{
 const f=setup();f.reader.scope.mockResolvedValueOnce({...scope(),checkpoint:{...selected,anchorMessageId:"reused-anchor"}});
 await f.broker.handle(f.request("Scope"));expect(f.post.mock.calls.at(-1)![0].outcome.status).toBe("error");await f.broker.handle(f.request("Diff"));expect(f.reader.diff).not.toHaveBeenCalled();
});
