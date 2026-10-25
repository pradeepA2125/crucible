import {expect,it,vi} from "vitest";
import {newOwner,registerOwner,forgetOwner,ownerFor} from "../src/runtime/project-owner.js";
import {healthBound,healthProof} from "../src/runtime/probe-health.js";
import {BackendGate} from "../src/backend-auth/backend-gate.js";
function fixture(port:number){
 const base=`http://127.0.0.1:${port}`,owner=newOwner("test","/project");owner.pid=42;registerOwner(port,owner);
 const session={protocol:1,workspace:"/project",process_id:"process",pid:42,client_id:owner.clientId,client_name:"test",role:"owner",trusted:false};
 let pid=42;let wait:Promise<void>|undefined;
 const fetch=vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes("/health?")){const nonce=new URL(url).searchParams.get("nonce")!;expect(new Headers(init?.headers).has("x-crucible-owner")).toBe(false);return new Response(JSON.stringify({pid,proof:healthProof("token",nonce),bound:healthBound("token",nonce,pid,"/project")}));}
  if(url.endsWith("/project/session")){await wait;return new Response(JSON.stringify(session));}
  return new Response("{}");
 });
 const gate=new BackendGate({fetch,probe:async()=>"authed",readToken:()=>"token",report:vi.fn(),now:Date.now});
 return {base,owner,session,fetch,send:gate.authedFetchFor(base),wrongProcess:()=>{pid=43;},hold:(promise:Promise<void>)=>{wait=promise;},close:()=>forgetOwner(port,owner)};
}
it("binds owner to verified endpoint/current process and never forwards observer writes",async()=>{
 const t=fixture(54321);try{
 await t.send(`${t.base}/v1/config`,{method:"PUT",body:"{}"});expect(t.fetch).toHaveBeenCalledTimes(3);expect(new Headers(t.fetch.mock.calls[2]![1]?.headers).get("x-crucible-owner")).toBe(t.owner.key);
 t.fetch.mockClear();await expect(t.send("http://127.0.0.1:54322/v1/config",{method:"PUT"})).rejects.toThrow("outside");expect(t.fetch).not.toHaveBeenCalled();
 t.session.pid=43;await expect(t.send(`${t.base}/v1/config`,{method:"PUT"})).rejects.toThrow("owner changed");expect(t.fetch).toHaveBeenCalledTimes(2);
 t.close();expect(ownerFor(t.base)).toBeUndefined();t.fetch.mockClear();await expect(t.send(`${t.base}/v1/config`,{method:"PUT"})).rejects.toThrow("observer");expect(t.fetch).not.toHaveBeenCalled();await t.send(`${t.base}/v1/config`);expect(t.fetch).toHaveBeenCalledOnce();
 }finally{t.close();}
});
it.each(["PUT","GET"])("does not disclose owner capability to a token-proven wrong process (%s)",async(method)=>{
 const t=fixture(54324);try{t.wrongProcess();await expect(t.send(`${t.base}/v1/project/session`,{method})).rejects.toThrow("owning process");expect(t.fetch).toHaveBeenCalledOnce();expect(new Headers(t.fetch.mock.calls[0]![1]?.headers).has("x-crucible-owner")).toBe(false);}finally{t.close();}
});
it.each(["cancel","retire"])("does not dispatch a waiting mutation after %s",async(kind)=>{
 const t=fixture(54323);let release!:()=>void;t.hold(new Promise<void>(resolve=>release=resolve));const abort=new AbortController();
 try{const pending=t.send(`${t.base}/v1/config`,{method:"PUT",signal:abort.signal});await vi.waitFor(()=>expect(t.fetch).toHaveBeenCalledTimes(2));if(kind==="cancel")abort.abort();else t.close();release();await expect(pending).rejects.toThrow();expect(t.fetch).toHaveBeenCalledTimes(2);}finally{t.close();}
});

it('owner replacement after proof completion cannot disclose replacement key in status probe',async()=>{
 const port=55679,base=`http://127.0.0.1:${port}`,old=newOwner('old','/project'),next=newOwner('new','/project');old.pid=42;next.pid=43;registerOwner(port,old);let received:string|null=null;
 const fetch=vi.fn(async(url:string,init?:RequestInit)=>{if(url.includes('/health?')){const n=new URL(url).searchParams.get('nonce')!;return new Response(JSON.stringify({pid:42,proof:healthProof('token',n),bound:healthBound('token',n,42,'/project')}));}received=new Headers(init?.headers).get('x-crucible-owner');return new Response('{}',{status:409});});
 const readToken=()=>{queueMicrotask(()=>queueMicrotask(()=>registerOwner(port,next)));return'token';};const gate=new BackendGate({fetch,probe:async()=>'authed',readToken,report:vi.fn(),now:Date.now});
 try{await expect(gate.fetchFor(base)(`${base}/v1/config`,{method:'PUT'})).rejects.toThrow();expect(received).not.toBe(next.key);expect(fetch).toHaveBeenCalledOnce();}finally{forgetOwner(port,old);forgetOwner(port,next);}
});
const tokenHooks=vi.hoisted(()=>({read:()=> 'token'}));vi.mock('../src/runtime/backend-token.js',()=>({readBackendToken:()=>tokenHooks.read()}));
import{createNodeProcessDeps}from'../src/runtime/node-runtime-deps.js';
it('raw adapter replacement after proof cannot disclose unproven replacement key',async()=>{
 const port=55680,base=`http://127.0.0.1:${port}`,old=newOwner('old','/project'),next=newOwner('new','/project');old.pid=42;next.pid=43;registerOwner(port,old);let received:string|null=null;tokenHooks.read=()=>{queueMicrotask(()=>queueMicrotask(()=>registerOwner(port,next)));return'token';};
 const fetch=vi.fn(async(url:string,init?:RequestInit)=>{if(url.includes('/health?')){const n=new URL(url).searchParams.get('nonce')!;return new Response(JSON.stringify({pid:42,proof:healthProof('token',n),bound:healthBound('token',n,42,'/project')}));}received=new Headers(init?.headers).get('x-crucible-owner');return new Response('{}',{status:409});});vi.stubGlobal('fetch',fetch);
 try{const deps=createNodeProcessDeps('/runtime',{append(){},appendLine(){},show(){}});await expect(deps.fetchJson(`${base}/v1/config`,{method:'PUT'})).rejects.toThrow();expect(received).not.toBe(next.key);expect(fetch).toHaveBeenCalledOnce();}finally{forgetOwner(port,old);forgetOwner(port,next);tokenHooks.read=()=>'token';vi.unstubAllGlobals();}
});
