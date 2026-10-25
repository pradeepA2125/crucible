import { EventEmitter } from "node:events";
import { describe,it,expect } from "vitest";
import { Terminals,shellEnvironment,type TerminalWorker } from "../src/terminals.js";
class Worker extends EventEmitter implements TerminalWorker {
  id="";frame=0;sent:any[]=[];
  postMessage(m:any){this.sent.push(m);if(m.type==="start"){this.id=m.id;queueMicrotask(()=>this.emit("message",{type:"ready",id:m.id,pid:12}));}if(m.type==="end")queueMicrotask(()=>{this.emit("message",{type:"exited",id:this.id,exitCode:0});this.emit("exit",0);});}
  kill(){this.emit("exit",1);return true;}
  data(value:Buffer){for(let n=0;n<value.length;n+=32768)this.emit("message",{type:"data",id:this.id,seq:++this.frame,data:value.subarray(n,n+32768).toString("base64")});}
}
describe("Your shell registry",()=>{
 it("isolates projects, caps output, validates its immutable lease and tracks idle/removal occupancy",async()=>{
  const workers:Worker[]=[];const registry=new Terminals(()=>{const w=new Worker();workers.push(w);return w;});let owner=true;
  const a=await registry.start("alpha","/private/tmp/alpha",{current:()=>owner}),b=await registry.start("beta","/private/tmp/beta",{current:()=>true});
  expect(registry.busy("alpha")).toBe(true);expect(()=>registry.read("beta",a.id,0)).toThrow();expect(registry.list("alpha").map(s=>s.id)).toEqual([a.id]);
  workers[0]!.data(Buffer.alloc(1024*1024+100,65));const read=registry.read("alpha",a.id,0);expect(read.reset).toBe(true);expect(read.session.start).toBe(100);expect(Buffer.from(read.data,"base64").length).toBe(65536);
  registry.input("alpha",a.id,"literal\\x03");expect(workers[0]!.sent.at(-1).chars).toBe("literal\\x03");owner=false;expect(()=>registry.input("alpha",a.id,"bad")).toThrow();expect(()=>registry.forget("alpha",a.id)).toThrow();
  await registry.end("alpha",a.id);expect(registry.busy("alpha")).toBe(false);expect(registry.list("beta")[0]!.id).toBe(b.id);registry.forget("alpha",a.id);await registry.dispose();expect(registry.list("beta")).toEqual([]);
 });
 it("has physical session and input caps; never silently replays on crash",async()=>{
  const workers:Worker[]=[];const registry=new Terminals(()=>{const w=new Worker();workers.push(w);return w;});const s=await registry.start("a","/a",{current:()=>true});
  for(let n=0;n<32;n++)registry.input("a",s.id,"a");expect(()=>registry.input("a",s.id,"a")).toThrow(/busy/);
  workers[0]!.emit("exit",2);expect(registry.list("a")[0]!.status).toBe("error");expect(()=>registry.input("a",s.id,"a")).toThrow();
  for(let n=0;n<3;n++)await registry.start("a","/a",{current:()=>true});await expect(registry.start("a","/a",{current:()=>true})).rejects.toThrow(/limit/);await registry.dispose();
 });
 it("passes only a native user environment without backend/provider capabilities",()=>{
  const env=shellEnvironment({HOME:"/home/user",PATH:"/usr/bin:/bin",LANG:"en_US.UTF-8",OPENAI_API_KEY:"secret",CRUCIBLE_OWNER_KEY:"owner",CRUCIBLE_INDEX_KEY:"index",CRUCIBLE_CHATGPT_REGISTRATION:"account",NODE_OPTIONS:"--require exploit",DYLD_INSERT_LIBRARIES:"bad",VIRTUAL_ENV:"backend",PYTHONPATH:"backend",ELECTRON_RUN_AS_NODE:"1"});
  expect(Object.keys(env).sort()).toEqual(["COLORTERM","HOME","LANG","PATH","TERM"].sort());
 });
});
