import{expect,it}from"vitest";import{randomUUID}from"node:crypto";
import{lifecycleReply,lifecyclePreferences}from"../src/lifecycle-protocol.js";import{parsePayload}from"../src/contract.js";
it("rejects missing names, truncated resource counts, oversized metadata and unknown fields",()=>{
 const row={thread_id:"t",title:"Title",turn_active:false,gates:0,gate_ids:[],agents_running:1,sessions_running:0,agents:[],teams:[],sessions:[]};
 const reply={version:1,request_id:randomUUID(),requested_workspace:"/root",supported:true,process_id:"p",workspace_path:"/root",threads:[row]};
 expect(lifecycleReply.safeParse(reply).success).toBe(false);row.agents_running=0;expect(lifecycleReply.safeParse(reply).success).toBe(true);
 expect(lifecycleReply.safeParse({...reply,threads:Array(1001).fill(row)}).success).toBe(false);
 expect(lifecycleReply.safeParse({...reply,secret:"no"}).success).toBe(false);
 expect(lifecyclePreferences.catch({notifications:"off",keepAwake:false}).parse({notifications:"invented"})).toEqual({notifications:"off",keepAwake:false});
});
it("validates finite settings and quit choices at the shared preload boundary",()=>{
 expect(parsePayload({type:"desktop/lifecycleChoice",snapshotId:randomUUID(),choice:"background"}).type).toBe("desktop/lifecycleChoice");
 expect(()=>parsePayload({type:"desktop/lifecycleChoice",snapshotId:"stale",choice:"force"})).toThrow();
 expect(()=>parsePayload({type:"desktop/lifecycleSet",preferences:{notifications:"off",keepAwake:true,trust:true}})).toThrow();
});
