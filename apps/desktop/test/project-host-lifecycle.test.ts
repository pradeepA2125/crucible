vi.mock("../../vscode-extension/src/runtime/backend-token.js",()=>({readBackendToken:()=>"token"}));
import{afterEach,expect,it,vi}from"vitest";import{mkdtempSync,mkdirSync,rmSync,realpathSync}from"node:fs";import{tmpdir}from"node:os";import{join}from"node:path";
import{createProjectHost}from"../src/host.js";import{newOwner,registerOwner,forgetOwner}from"../../vscode-extension/src/runtime/project-owner.js";import{healthBound,healthProof}from"../../vscode-extension/src/runtime/probe-health.js";
const dirs:string[]=[];afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks();dirs.splice(0).forEach(d=>rmSync(d,{recursive:true,force:true}));});
async function fixture(){const profile=realpathSync(mkdtempSync(join(tmpdir(),"cp11-host-")));dirs.push(profile);const root=join(profile,"root");mkdirSync(root);const port=55779,base=`http://127.0.0.1:${port}`,owner=newOwner("desktop",root);owner.pid=42;registerOwner(port,owner);const post=vi.fn();let reply:(url:string)=>Promise<Response>=async url=>new Response(JSON.stringify({version:1,supported:true,request_id:new URL(url).searchParams.get("request_id"),requested_workspace:root,workspace_path:root,process_id:"chat-process",threads:[{thread_id:"t",title:"Alpha",turn_active:true,gates:1,gate_ids:["gate"],agents_running:0,sessions_running:0,agents:[],teams:[{id:"team",label:"Review"}],sessions:[]}]}));
 const fetch=vi.fn(async(url:string)=>{if(url.includes("/health?")){const nonce=new URL(url).searchParams.get("nonce")!;return new Response(JSON.stringify({pid:42,proof:healthProof("token",nonce),bound:healthBound("token",nonce,42,root)}));}if(url.includes("/chat/lifecycle?"))return reply(url);return new Response(JSON.stringify({protocol:1,workspace:root,process_id:"owner-process",pid:42,client_id:owner.clientId,client_name:owner.name,role:"owner",trusted:false}));});vi.stubGlobal("fetch",fetch);
 const host=createProjectHost({root,profile,resources:profile,runtimeDir:join(profile,"runtime"),draftPersistence:()=>false,encryption:{isEncryptionAvailable:()=>false,encryptString:()=>Buffer.alloc(0),decryptString:()=>""},post,viewIdentity:()=>1,isOpen:()=>true,showChat(){},showSetup(){},showSettings(){},openExternal:async()=>{},revealFile:async()=>{},readTheme:()=>({appearance:"dark",accent:"blue"}),saveTheme:async()=>{},log(){},backendReady(){}});
 vi.spyOn(host.runtime,"backendUrl").mockReturnValue(base);(host.runtime as any).readyListeners[0](root);await vi.waitFor(()=>expect(post).toHaveBeenCalledWith(expect.objectContaining({type:"desktop/access",pid:42})));
 return{host,root,fetch,owner,port,setReply:(next:typeof reply)=>reply=next,close:async()=>{await host.dispose();forgetOwner(port,owner);}};}
it("actual host reads named lifecycle metadata without the transcript-heavy thread list and fences owner changes",async()=>{
 const h=await fixture();try{expect(await h.host.activity()).toMatchObject({state:"busy",attention:[{threadId:"t",title:"Alpha",gates:1}],evidence:{owner:true,threads:[{teams:[{id:"team",label:"Review"}]}]}});
 expect(h.fetch.mock.calls.some(([url])=>url.includes("/chat/threads"))).toBe(false);
 h.setReply(async url=>{forgetOwner(h.port,h.owner);return new Response(JSON.stringify({version:1,supported:false,request_id:new URL(url).searchParams.get("request_id"),requested_workspace:h.root,reason:"legacy_backend"}));});
 expect(await h.host.activity()).toEqual({state:"unknown",attention:[]});}finally{await h.close();}
});
it("visible deadline keeps the same physical read slot until a late reply actually settles",async()=>{
 const h=await fixture();let release!:(response:Response)=>void;h.setReply(()=>new Promise(r=>release=r));vi.useFakeTimers();try{
 const pending=h.host.activity();await vi.advanceTimersByTimeAsync(0);await vi.advanceTimersByTimeAsync(12001);expect(await pending).toEqual({state:"unknown",attention:[]});
 expect(await h.host.activity()).toEqual({state:"unknown",attention:[]});expect(h.fetch.mock.calls.filter(([url])=>url.includes("/chat/lifecycle?"))).toHaveLength(1);
 release(new Response("{}"));await vi.advanceTimersByTimeAsync(0);h.setReply(async()=>new Response("{}"));await h.host.activity();expect(h.fetch.mock.calls.filter(([url])=>url.includes("/chat/lifecycle?"))).toHaveLength(2);
 }finally{release?.(new Response("{}"));await h.close();}
});
