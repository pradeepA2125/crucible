import { afterEach, expect, it, vi } from "vitest";
import { createBaselineClient, type BaselinePort } from "../desktop/baselineClient";
const generation="12345678-1234-4234-8234-123456789abc", chosen={seq:0,anchorMessageId:"anchor",createdAt:"date"};
const scope={state:"ready",generation,checkpoint:chosen,changes:["a.ts","b.ts","c.ts"].map(path=>({path,status:"M",fromSeq:0,supplying:chosen})),unchanged:0,createdRemoved:0,uncompared:[],omitted:0,partial:false};
const listing={state:"ready",checkpoints:[chosen],omitted:0}, binary={kind:"binary",fingerprint:"a".repeat(64)};
const clients:BaselinePort[]=[];afterEach(()=>{clients.splice(0).forEach(client=>client.dispose());vi.useRealTimers();});
const event=(data:unknown)=>window.dispatchEvent(new MessageEvent("message",{data,source:window,origin:"app://app"}));
const context=(threadId:string|null="A",viewId="view",projectId="project")=>event({type:"desktop/baselineContext",threadId,viewId,projectId});
const setup=()=>{const send=vi.fn(),client=createBaselineClient(send);clients.push(client);return{send,client};};
const reply=(send:ReturnType<typeof vi.fn>,index:number,outcome:unknown,extra:Record<string,unknown>={})=>{
 const request=send.mock.calls[index]![0];event({type:"desktop/baselineResult",viewId:"view",projectId:"project",threadId:request.threadId,requestId:request.requestId,
 operation:request.type==="desktop/baselineList"?"list":request.type==="desktop/baselineScope"?"scope":"diff",...(request.type==="desktop/baselineDiff"?{generation:request.generation,path:request.path}:{}),outcome,...extra});
};
const ready=async(f:ReturnType<typeof setup>)=>{context();const pending=f.client.scope(chosen);reply(f.send,f.send.mock.calls.length-1,{status:"ok",result:scope});await pending;};
it("is lazy, requires actual context and open conversation, locally validates requests",async()=>{
 const f=setup();expect(f.send).not.toHaveBeenCalled();await expect(f.client.list()).rejects.toMatchObject({reason:"project"});
 event({type:"desktop/project",projectId:"project"});await expect(f.client.list()).rejects.toMatchObject({reason:"project"});
 context(null);await expect(f.client.list()).rejects.toMatchObject({reason:"thread"});context();await expect(f.client.scope({...chosen,seq:-1})).rejects.toMatchObject({reason:"unsupported"});
 await expect(f.client.diff(generation,"../outside")).rejects.toMatchObject({reason:"unsupported"});await expect(f.client.diff("HEAD","a.ts")).rejects.toMatchObject({reason:"stale"});expect(f.send).not.toHaveBeenCalled();
});
it("coalesces list and identical scope, reuses thread list during scan, Refresh invalidates it",async()=>{
 const f=setup();context();const first=f.client.list();expect(f.client.list()).toBe(first);reply(f.send,0,{status:"ok",result:listing});await first;
 const scan=f.client.scope(chosen);expect(f.client.scope({...chosen})).toBe(scan);await expect(f.client.list()).resolves.toEqual(listing);expect(f.send).toHaveBeenCalledTimes(2);
 await expect(f.client.scope({...chosen,seq:1})).rejects.toMatchObject({reason:"busy"});await expect(f.client.list(true)).rejects.toMatchObject({reason:"busy"});reply(f.send,1,{status:"ok",result:scope});await scan;
 const refreshed=f.client.list();expect(f.send).toHaveBeenCalledTimes(3);reply(f.send,2,{status:"ok",result:listing});await refreshed;
});
it("runs metadata independently and dispatches only the newest queued diff",async()=>{
 const f=setup();await ready(f);const a=f.client.diff(generation,"a.ts"),b=f.client.diff(generation,"b.ts"),discard=expect(b).rejects.toMatchObject({reason:"superseded"});
 const c=f.client.diff(generation,"c.ts");await discard;expect(f.client.diff(generation,"c.ts")).toBe(c);
 const list=f.client.list();reply(f.send,2,{status:"ok",result:listing});await list;expect(f.send).toHaveBeenCalledTimes(3);
 reply(f.send,1,{status:"ok",result:binary});await a;expect(f.send.mock.calls[3]![0].path).toBe("c.ts");reply(f.send,3,{status:"ok",result:{kind:"too-large"}});await c;
 const again=f.client.diff(generation,"a.ts"),queued=f.client.diff(generation,"b.ts"),replaced=expect(queued).rejects.toMatchObject({reason:"superseded"});expect(f.client.diff(generation,"a.ts")).toBe(again);await replaced;reply(f.send,4,{status:"ok",result:binary});await again;expect(f.send).toHaveBeenCalledTimes(5);
});
it("clears thread state immediately, holds abandoned occupancy until old reply acknowledges, never revives old data",async()=>{
 const f=setup();await ready(f);const changed=vi.fn();f.client.onContextChange(changed);const old=f.client.scope(chosen),cancelled=expect(old).rejects.toMatchObject({reason:"cancelled"});
 context("B");await cancelled;expect(changed).toHaveBeenCalledWith({viewId:"view",projectId:"project",threadId:"B"});await expect(f.client.list()).rejects.toMatchObject({reason:"busy"});
 reply(f.send,1,{status:"ok",result:scope});await expect(f.client.diff(generation,"a.ts")).rejects.toMatchObject({reason:"stale"});
 const next=f.client.list();expect(f.send.mock.calls[2]![0].threadId).toBe("B");reply(f.send,2,{status:"ok",result:listing});await next;
 const copy=f.client.context()!;copy.threadId="caller mutation";expect(f.client.context()?.threadId).toBe("B");
});
it("rejects and drops queued previous-thread detail while resolving cancellation by request id",async()=>{
 const f=setup();await ready(f);const a=f.client.diff(generation,"a.ts"),b=f.client.diff(generation,"b.ts");const ca=expect(a).rejects.toMatchObject({reason:"cancelled"}),cb=expect(b).rejects.toMatchObject({reason:"cancelled"});
 context("B");await ca;await cb;reply(f.send,1,{status:"cancelled",message:"Changed"},{threadId:"B"});expect(f.send).toHaveBeenCalledTimes(2);
 const scan=f.client.scope(chosen);reply(f.send,2,{status:"ok",result:scope});await scan;const next=f.client.diff(generation,"a.ts");reply(f.send,3,{status:"ok",result:binary});await next;
});
it("holds logical timeout until acknowledgment without accepting expired scope or cascading queued work",async()=>{
 vi.useFakeTimers();const f=setup();context();const scan=f.client.scope(chosen),timeout=expect(scan).rejects.toMatchObject({reason:"timeout"});await vi.advanceTimersByTimeAsync(45001);await timeout;
 await expect(f.client.scope(chosen)).rejects.toMatchObject({reason:"busy"});expect(f.send).toHaveBeenCalledTimes(1);reply(f.send,0,{status:"ok",result:scope});await expect(f.client.diff(generation,"a.ts")).rejects.toMatchObject({reason:"stale"});
 const fresh=f.client.scope(chosen);reply(f.send,1,{status:"ok",result:scope});await fresh;
 const a=f.client.diff(generation,"a.ts"),b=f.client.diff(generation,"b.ts"),late=expect(a).rejects.toMatchObject({reason:"timeout"});await vi.advanceTimersByTimeAsync(45001);await late;expect(f.send).toHaveBeenCalledTimes(3);
 reply(f.send,2,{status:"ok",result:binary});expect(f.send).toHaveBeenCalledTimes(4);reply(f.send,3,{status:"ok",result:binary});await b;
});
it("fails at 45+15 seconds when an acknowledgment is lost, including after thread change",async()=>{
 vi.useFakeTimers();const f=setup();context();const scan=f.client.list(),cancel=expect(scan).rejects.toMatchObject({reason:"cancelled"});context("B");await cancel;
 await vi.advanceTimersByTimeAsync(60001);await expect(f.client.list()).rejects.toMatchObject({reason:"unavailable"});expect(f.send).toHaveBeenCalledTimes(1);
});
it.each(["busy","timeout","cancelled","error","too-large"])("preserves typed %s without retries or poisoning the client",async status=>{
 const f=setup();context();const pending=f.client.list(),refused=expect(pending).rejects.toMatchObject({reason:status});reply(f.send,0,{status,message:"Controlled reason"});await refused;expect(f.send).toHaveBeenCalledTimes(1);
 const next=f.client.list();reply(f.send,1,{status:"ok",result:listing});await next;
});
it("rejects wrong operation, malformed envelope, wrong checkpoint and wrong diff path",async()=>{
 for(const attack of ["operation","malformed","checkpoint","path"]){
  const f=setup();await ready(f);const job=attack==="path"?f.client.diff(generation,"a.ts"):f.client.scope(chosen),invalid=expect(job).rejects.toMatchObject({reason:"invalid"});
  if(attack==="operation")reply(f.send,1,{status:"ok",result:listing},{operation:"list"});
  else if(attack==="malformed")reply(f.send,1,{status:"ok",result:scope},{extra:true});
  else if(attack==="checkpoint")reply(f.send,1,{status:"ok",result:{...scope,checkpoint:{...chosen,anchorMessageId:"reused"}}});
  else reply(f.send,1,{status:"ok",result:binary},{path:"b.ts"});
  await invalid;await expect(f.client.list()).rejects.toMatchObject({reason:"invalid"});
 }
});
it("does not render successful cross-view/project/thread results or accept unknown replies",async()=>{
 const f=setup();context();for(const extra of [{viewId:"other"},{projectId:"other"},{threadId:"other"}]){
 const pending=f.client.list(),cancelled=expect(pending).rejects.toMatchObject({reason:"cancelled"});reply(f.send,f.send.mock.calls.length-1,{status:"ok",result:listing},extra);await cancelled;expect(f.client.context()).toEqual({viewId:"view",projectId:"project",threadId:"A"});}
 const next=f.client.list();let settled=false;void next.then(()=>{settled=true;});event({type:"desktop/baselineResult",requestId:"unknown"});await Promise.resolve();expect(settled).toBe(false);reply(f.send,3,{status:"ok",result:listing});await next;
});
it("expires evicted running and queued generations and consumes no new host slot for the queued one",async()=>{
 const f=setup();await ready(f);const a=f.client.diff(generation,"a.ts"),b=f.client.diff(generation,"b.ts"),sa=expect(a).rejects.toMatchObject({reason:"stale"}),sb=expect(b).rejects.toMatchObject({reason:"stale"});
 for(const gen of ["12345678-1234-4234-8234-123456789abd","12345678-1234-4234-8234-123456789abe"]){const scan=f.client.scope(chosen);reply(f.send,f.send.mock.calls.length-1,{status:"ok",result:{...scope,generation:gen}});await scan;}
 reply(f.send,1,{status:"ok",result:binary});await sa;await sb;expect(f.send).toHaveBeenCalledTimes(4);
});
it("handles view/project changes and disposal without reviving cached checkpoints",async()=>{
 const f=setup();await ready(f);const pending=f.client.list(),cancelled=expect(pending).rejects.toMatchObject({reason:"cancelled"});context("A","next-view","other-project");await cancelled;reply(f.send,1,{status:"ok",result:listing});
 const callback=vi.fn();f.client.onContextChange(callback);const current=f.client.list(),disposed=expect(current).rejects.toMatchObject({reason:"cancelled"});f.client.dispose();await disposed;context("B");expect(callback).not.toHaveBeenCalled();await expect(f.client.list()).rejects.toMatchObject({reason:"cancelled"});
});
it("fails safely on sender failure before dispatch and validates generated UUIDs locally",async()=>{
 const send=vi.fn(()=>{throw Error("offline");}),client=createBaselineClient(send);clients.push(client);context();await expect(client.list()).rejects.toMatchObject({reason:"unavailable"});await expect(client.list()).rejects.toMatchObject({reason:"unavailable"});expect(send).toHaveBeenCalledTimes(1);
 const f=setup();context();const spy=vi.spyOn(crypto,"randomUUID").mockReturnValueOnce("invalid" as ReturnType<typeof crypto.randomUUID>);await expect(f.client.list()).rejects.toMatchObject({reason:"unavailable"});expect(f.send).not.toHaveBeenCalled();spy.mockRestore();
});

it("ignores foreign-window and wrong-origin context/results, while accepting the real preload window",async()=>{
 const f=setup();context();const pending=f.client.list();let settled=false;void pending.then(()=>{settled=true;});
 const request=f.send.mock.calls[0]![0];
 for(const source of [null,window])for(const origin of ["https://foreign.example","app://app"]){
  if(source===window&&origin==="app://app")continue;
  window.dispatchEvent(new MessageEvent("message",{source,origin,data:{type:"desktop/baselineContext",viewId:"forged",projectId:"forged",threadId:"forged"}}));
  window.dispatchEvent(new MessageEvent("message",{source,origin,data:{type:"desktop/baselineResult",viewId:"view",projectId:"project",threadId:"A",requestId:request.requestId,operation:"list",outcome:{status:"ok",result:listing}}}));
 }
 await Promise.resolve();expect(settled).toBe(false);expect(f.client.context()).toEqual({viewId:"view",projectId:"project",threadId:"A"});reply(f.send,0,{status:"ok",result:listing});await pending;
});
