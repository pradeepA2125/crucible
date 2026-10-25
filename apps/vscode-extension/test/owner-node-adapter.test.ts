import {expect,it,vi,afterEach} from "vitest";
const tokenReader=vi.hoisted(()=>({read:()=>"token"}));
vi.mock("../src/runtime/backend-token.js",()=>({readBackendToken:()=>tokenReader.read()}));
import {createNodeProcessDeps} from "../src/runtime/node-runtime-deps.js";
import {newOwner,registerOwner,forgetOwner} from "../src/runtime/project-owner.js";
import {healthProof,healthBound} from "../src/runtime/probe-health.js";
afterEach(()=>{vi.unstubAllGlobals();tokenReader.read=()=>"token";});
it.each(["success","wrong-process","retired"])("raw trusted adapter checks process binding before credential disclosure: %s",async(mode)=>{
 const base="http://127.0.0.1:54329",owner=newOwner("test","/project");owner.pid=42;registerOwner(54329,owner);
 const fetch=vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes("/health?")){expect(new Headers(init?.headers).has("x-crucible-owner")).toBe(false);const n=new URL(url).searchParams.get("nonce")!,pid=mode==="wrong-process"?43:42;return new Response(JSON.stringify({pid,proof:healthProof("token",n),bound:healthBound("token",n,pid,"/project")}));}
  const h=new Headers(init?.headers);expect(h.get("authorization")).toBe("Bearer token");expect(h.get("x-crucible-owner")).toBe(owner.key);
  if(url.endsWith("/project/session")){if(mode==="retired")forgetOwner(54329,owner);return new Response(JSON.stringify({protocol:1,workspace:"/project",pid:42,process_id:"p",client_id:owner.clientId,client_name:"test",role:"owner",trusted:false}));}
  return new Response("{}");
 });vi.stubGlobal("fetch",fetch);
 const deps=createNodeProcessDeps("/runtime",{append(){},appendLine(){},show(){}});
 try{const work=deps.fetchJson(`${base}/v1/index/build`,{method:"POST",body:"{}"});if(mode==="success"){await expect(work).resolves.toEqual({});expect(fetch).toHaveBeenCalledTimes(3);}else {await expect(work).rejects.toThrow();expect(fetch).toHaveBeenCalledTimes(mode==="wrong-process"?1:2);}}finally{forgetOwner(54329,owner);}
});
it("raw adapter refuses observer mutations before any request",async()=>{
 const fetch=vi.fn();vi.stubGlobal("fetch",fetch);const deps=createNodeProcessDeps("/runtime",{append(){},appendLine(){},show(){}});await expect(deps.fetchJson("http://127.0.0.1:54330/v1/index/build",{method:"POST"})).rejects.toThrow("observer");expect(fetch).not.toHaveBeenCalled();
});

it("raw adapter cannot disclose a replacement key between proof resolution and caller continuation",async()=>{
 const port=54331,base=`http://127.0.0.1:${port}`,old=newOwner("old","/project"),next=newOwner("new","/project");old.pid=42;next.pid=43;registerOwner(port,old);let received:string|null=null;
 const fetch=vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes("/health?")){const n=new URL(url).searchParams.get("nonce")!;return new Response(JSON.stringify({pid:42,proof:healthProof("token",n),bound:healthBound("token",n,42,"/project")}));}
  received=new Headers(init?.headers).get("x-crucible-owner");return new Response("{}",{status:409});
 });vi.stubGlobal("fetch",fetch);tokenReader.read=()=>{queueMicrotask(()=>queueMicrotask(()=>registerOwner(port,next)));return "token";};
 try{const deps=createNodeProcessDeps("/runtime",{append(){},appendLine(){},show(){}});await expect(deps.fetchJson(`${base}/v1/index/build`,{method:"POST"})).rejects.toThrow();expect(received).not.toBe(next.key);expect(fetch).toHaveBeenCalledOnce();}finally{forgetOwner(port,old);forgetOwner(port,next);}
});
