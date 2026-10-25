import { StrictMode } from "react";
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createBaselineClient, type BaselinePort } from "../desktop/baselineClient";
import { createBaselineReviewAdapter } from "../desktop/baselineReviewAdapter";
import { useGitChanges } from "../desktop/useGitChanges";
const generation="12345678-1234-4234-8234-123456789abc",chosen={seq:0,anchorMessageId:"anchor",createdAt:"date"};
const scope={state:"ready",generation,checkpoint:chosen,changes:["a.ts","b.ts","c.ts"].map(path=>({path,status:"M",fromSeq:0,supplying:chosen})),unchanged:1,createdRemoved:1,uncompared:[],omitted:0,partial:false};
const text=(fingerprint="a".repeat(64))=>({kind:"text",unified:"@@ -1 +1 @@\n-old\n+new",additions:1,deletions:1,truncated:false,fingerprint});
const clients:BaselinePort[]=[];afterEach(()=>{clients.splice(0).forEach(client=>client.dispose());});
const event=(data:unknown)=>window.dispatchEvent(new MessageEvent("message",{data,source:window,origin:"app://app"}));
const context=(threadId="A")=>event({type:"desktop/baselineContext",viewId:"view",projectId:"project",threadId});
const setup=()=>{
 const send=vi.fn(),port=createBaselineClient(send);clients.push(port);context();const adapter=createBaselineReviewAdapter(port);
 const hook=renderHook(()=>useGitChanges(adapter.review),{wrapper:StrictMode});
 const reply=(index:number,result:unknown,status="ok")=>{const request=send.mock.calls[index]![0];event({type:"desktop/baselineResult",requestId:request.requestId,viewId:"view",projectId:"project",threadId:request.threadId,
 operation:request.type==="desktop/baselineScope"?"scope":"diff",...(request.type==="desktop/baselineDiff"?{generation:request.generation,path:request.path}:{}),outcome:status==="ok"?{status,result}:{status,message:"Controlled reason"}});};
 const refresh=async(value:unknown=scope)=>{let work!:Promise<void>;act(()=>{work=hook.result.current.refresh();});await act(async()=>reply(send.mock.calls.length-1,value));if(send.mock.calls.at(-1)![0].type==="desktop/baselineDiff")await act(async()=>{reply(send.mock.calls.length-1,text());await work;});else await act(async()=>work);};
 return {send,port,adapter,...hook,reply,refresh};
};
it("reuses the existing hook, is lazy, keeps source provenance separate and caches inspected details",async()=>{
 const f=setup();expect(f.send).not.toHaveBeenCalled();await act(async()=>f.result.current.refresh());expect(f.result.current.result).toMatchObject({state:"unavailable"});expect(f.send).not.toHaveBeenCalled();
 act(()=>f.adapter.choose(chosen));await f.refresh();expect(f.result.current.detail?.result).toEqual(text());expect(f.adapter.result()).toEqual(scope);expect(f.result.current.result).toMatchObject({state:"ready",warnings:[],uncompared:[]});expect(f.result.current.result).not.toHaveProperty("head");
 act(()=>f.result.current.select("a.ts"));expect(f.send).toHaveBeenCalledTimes(2);expect(f.result.current.details?.size).toBe(1);
});
it("keys Viewed and cached detail by full conversation/checkpoint identity, including reused seq",async()=>{
 const f=setup();act(()=>f.adapter.choose(chosen));await f.refresh();act(()=>f.result.current.setViewed("a.ts",true));expect(f.result.current.isViewed("a.ts")).toBe(true);
 act(()=>f.adapter.choose({...chosen}));expect(f.result.current.isViewed("a.ts")).toBe(true);
 act(()=>f.adapter.choose({...chosen,anchorMessageId:"reused",createdAt:"new-date"}));expect(f.result.current.result).toBeUndefined();expect(f.result.current.isViewed("a.ts")).toBe(false);expect(f.result.current.details?.size).toBe(0);
 act(()=>context("B"));expect(f.adapter.selected()).toBeUndefined();expect(f.adapter.result()).toBeUndefined();expect(f.result.current.result).toBeUndefined();
});
it("keeps Viewed pending on Refresh until the new fingerprint is inspected, then invalidates changed bytes",async()=>{
 const f=setup();act(()=>f.adapter.choose(chosen));await f.refresh();act(()=>f.result.current.setViewed("a.ts",true));
 let work!:Promise<void>;act(()=>{work=f.result.current.refresh();});await act(async()=>f.reply(2,{...scope,generation:"12345678-1234-4234-8234-123456789abd"}));expect(f.result.current.needsRecheck("a.ts")).toBe(true);expect(f.result.current.isViewed("a.ts")).toBe(false);
 await act(async()=>{f.reply(3,text("b".repeat(64)));await work;});expect(f.result.current.needsRecheck("a.ts")).toBe(false);expect(f.result.current.isViewed("a.ts")).toBe(false);
});
it("translates real superseded client flow so cancelled sections do not become permanent cached errors",async()=>{
 const f=setup();act(()=>f.adapter.choose(chosen));let work!:Promise<void>;act(()=>{work=f.result.current.refresh();});await act(async()=>f.reply(0,scope));
 await act(async()=>{f.result.current.select("b.ts");f.result.current.select("c.ts");});
 expect(f.result.current.details?.get("b.ts")).toBeUndefined();expect(f.send).toHaveBeenCalledTimes(2);
 await act(async()=>{f.reply(1,text());await work;});expect(f.send.mock.calls[2]![0].path).toBe("c.ts");
 await act(async()=>f.result.current.select("b.ts"));await act(async()=>f.reply(2,text()));expect(f.send.mock.calls[3]![0].path).toBe("b.ts");
 await act(async()=>f.reply(3,text()));expect(f.result.current.detail?.result).toEqual(text());expect(f.result.current.details?.get("b.ts")?.loading).toBe(false);expect(f.result.current.details?.get("b.ts")?.error).toBeUndefined();
});

it("drops old scope facts and file bytes after actual thread or checkpoint changes",async()=>{
 const f=setup();act(()=>f.adapter.choose(chosen));let work!:Promise<void>;act(()=>{work=f.result.current.refresh();});act(()=>context("B"));
 await act(async()=>{f.reply(0,scope);await work;});expect(f.adapter.result()).toBeUndefined();expect(f.result.current.result).toBeUndefined();expect(f.result.current.details?.size).toBe(0);
 act(()=>f.adapter.choose(chosen));await f.refresh();let read!:Promise<void>;act(()=>{read=f.result.current.load!("b.ts");});act(()=>f.adapter.choose({...chosen,createdAt:"different"}));await act(async()=>{f.reply(f.send.mock.calls.length-1,text());await read;});expect(f.result.current.details?.size).toBe(0);
});
it("retains honest partial evidence and uses the existing bounded single-file mode",async()=>{
 const f=setup();act(()=>f.adapter.choose(chosen));const value={...scope,partial:true,omitted:4,uncompared:[{path:"blocked",reason:"Current file is a link."}]};await f.refresh(value);
 expect(f.adapter.result()).toEqual(value);expect(f.result.current.large).toBe(true);expect(f.result.current.result).toMatchObject({state:"ready",partial:true});expect(f.result.current.result).not.toHaveProperty("head");
});
it("subscribes lazily without StrictMode render leaks and releases only its own subscription",()=>{
 const f=setup();const subscribe=vi.spyOn(f.port,"onContextChange"),unused=createBaselineReviewAdapter(f.port);expect(subscribe).not.toHaveBeenCalled();unused.dispose();
 const copy=f.adapter.selected();expect(copy).toBeUndefined();f.unmount();const callback=vi.fn();const remove=f.port.onContextChange(callback);context("B");expect(callback).toHaveBeenCalledTimes(1);remove();
});
