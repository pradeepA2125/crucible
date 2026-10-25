import{expect,it,vi}from"vitest";import{ProjectPool,type ProjectActivity}from"../src/project-pool.js";
function host(){let allowed=true,status:ProjectActivity={state:"idle",attention:[]};return{canDetach:()=>allowed,detach:vi.fn(),flush:vi.fn(),activity:vi.fn(async()=>status),pause:vi.fn(async()=>true),dispose:vi.fn(async()=>{}),block:()=>allowed=false,set:(v:ProjectActivity)=>status=v};}
it("retains A→B→A host state and flushes before detach; admissions starting during flush refuse navigation",async()=>{
 const a=host(),b=host(),pool=new ProjectPool();pool.add("a",a);pool.add("b",b);const attach=vi.fn(async()=>{});
 await pool.select("a",async()=>{},attach);await pool.select("b",async()=>{},attach);await pool.select("a",async()=>{},attach);expect(pool.get("a")).toBe(a);expect(a.detach).toHaveBeenCalledOnce();
 await expect(pool.select("b",async()=>{a.block();},attach)).rejects.toThrow("saving");expect(pool.selected).toBe("a");
});
it("busy, gates, unknown and actual unsettled reads prevent idle pause; only measured background idle pauses",async()=>{
 let clock=0;const a=host(),b=host(),pool=new ProjectPool(()=>clock);pool.add("a",a);pool.add("b",b);await pool.select("a",async()=>{},async()=>{});
 b.set({state:"busy",attention:[{threadId:"t",title:"Needs review",gates:1}]});await pool.poll();clock=600000;await pool.poll();expect(b.pause).not.toHaveBeenCalled();
 b.set({state:"unknown",attention:[]});await pool.poll();clock+=600000;await pool.poll();expect(b.pause).not.toHaveBeenCalled();
 b.set({state:"idle",attention:[]});await pool.poll();clock+=299999;await pool.poll();expect(b.pause).not.toHaveBeenCalled();clock++;await pool.poll();expect(b.pause).toHaveBeenCalledOnce();expect(a.pause).not.toHaveBeenCalled();
});
it("a held read owns its slot through real settlement and paused removal never stops another host",async()=>{
 const a=host(),b=host(),pool=new ProjectPool(()=>0,2);pool.add("a",a);pool.add("b",b);await pool.select("a",async()=>{},async()=>{});
 let finish!:(v:ProjectActivity)=>void;b.activity.mockImplementationOnce(()=>new Promise(r=>finish=r));const held=pool.poll();await pool.poll();expect(b.activity).toHaveBeenCalledOnce();await expect(pool.remove("b")).rejects.toThrow("status check");
 expect(()=>pool.add("c",host())).toThrow("Eight projects");finish({state:"idle",attention:[]});await held;await pool.remove("b");expect(b.dispose).toHaveBeenCalledOnce();expect(a.dispose).not.toHaveBeenCalled();
});
it("reserves the idle pause across awaits so a project cannot be selected while its child is stopping",async()=>{
 let clock=0;const a=host(),b=host(),pool=new ProjectPool(()=>clock);pool.add("a",a);pool.add("b",b);await pool.select("a",async()=>{},async()=>{});await pool.poll();clock=300001;
 let finish!:(v:boolean)=>void;b.pause.mockImplementationOnce(()=>new Promise(r=>finish=r));const pausing=pool.poll();for(let i=0;i<10;i++)await Promise.resolve();
 await expect(pool.select("b",async()=>{},async()=>{})).rejects.toThrow("in progress");finish(true);await pausing;expect(pool.selected).toBe("a");expect(pool.summaries().find(s=>s.id==="b")?.paused).toBe(true);
});
