vi.mock("../../vscode-extension/src/runtime/backend-token.js",()=>({readBackendToken:()=>"token"}));
import{newOwner,registerOwner,forgetOwner}from"../../vscode-extension/src/runtime/project-owner.js";
import{healthBound,healthProof}from"../../vscode-extension/src/runtime/probe-health.js";
import{afterEach,expect,it,vi}from"vitest";import{mkdtempSync,mkdirSync,rmSync,readFileSync,writeFileSync}from"node:fs";import{realpath,stat}from"node:fs/promises";import{tmpdir,homedir}from"node:os";import{join}from"node:path";import{transformSync}from"esbuild";
import{createProjectHost}from"../src/host.js";import{ProjectPool}from"../src/project-pool.js";import{ProjectRegistry,projectId}from"../src/project-registry.js";import{CrucibleController}from"../../vscode-extension/src/controller.js";
const dirs:string[]=[];afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();dirs.splice(0).forEach(d=>rmSync(d,{recursive:true,force:true}));});
function fixture(extra:Record<string,unknown>={}){const profile=mkdtempSync(join(tmpdir(),"cp9-host-"));dirs.push(profile);const root=join(profile,"root");mkdirSync(root);return createProjectHost({root,profile,resources:profile,runtimeDir:join(profile,"runtime"),draftPersistence:()=>false,encryption:{isEncryptionAvailable:()=>false,encryptString:()=>{throw Error("off")},decryptString:()=>{throw Error("off")}},post:vi.fn(),viewIdentity:()=>1,isOpen:()=>true,showChat(){},showSetup(){},showSettings(){},openExternal:async()=>{},revealFile:async()=>{},readTheme:()=>({appearance:"dark",accent:"blue"}),saveTheme:async()=>{},log(){},backendReady(){},...extra});}
it("actual host routes preparation/unknown admission as locked but lets accepted headers outlive A→B attachment without Stop",async()=>{
 let resolveBody!:()=>void,admit!:()=>void,unknown!:()=>void,known!:()=>void;const held=new Promise<void>(r=>resolveBody=r);
 vi.spyOn(CrucibleController.prototype,"sendChatMessage").mockImplementation(async function(this:CrucibleController,...args){const ui=(this as any).ui;admit=()=>ui.draftAccepted(args[6],"thread");unknown=()=>ui.renderChatAdmission({threadId:"thread",state:"unknown",checking:false});known=()=>ui.renderChatAdmission(null);await held;});
 const stop=vi.spyOn(CrucibleController.prototype,"stopActiveTurn");const a=fixture(),b=fixture(),pool=new ProjectPool();pool.add("a",a);pool.add("b",b);await pool.select("a",async()=>{},async()=>{});
 try{
  await a.handle({type:"draft/read",requestId:"r",threadId:"new"},false);
  await a.handle({type:"draft/begin",requestId:"b",threadId:"new",revision:1,draftId:"send",draft:{text:"unsent",imageAttachments:[],mentionedPaths:[]}},false);
  const sending=a.handle({type:"sendMessage",draftId:"send",text:"unsent"},false);await vi.waitFor(()=>expect(admit).toBeDefined());expect(a.canDetach()).toBe(false);
  admit();unknown();await expect(pool.select("b",async()=>{},async()=>{})).rejects.toThrow("pending");known();expect(a.canDetach()).toBe(true);
  await pool.select("b",async()=>{},async()=>{});expect(pool.selected).toBe("b");expect(stop).not.toHaveBeenCalled();resolveBody();await sending;
 }finally{resolveBody();await pool.dispose();}
});
it("host consumes exact attention target only after fresh matching readiness and retires stale mounts",async()=>{
 let finish!:()=>void,view=1;const open=vi.spyOn(CrucibleController.prototype,"openChat").mockImplementationOnce(()=>new Promise(r=>finish=r)).mockResolvedValue(undefined);
 const switchThread=vi.spyOn(CrucibleController.prototype,"switchChatThread").mockResolvedValue(undefined),target=vi.fn(()=>"B2");const h=fixture({viewIdentity:()=>view,takeThreadTarget:target});
 try{const old=h.handle({type:"webviewReady"},false);await vi.waitFor(()=>expect(open).toHaveBeenCalledOnce());view=2;finish();await old;expect(target).not.toHaveBeenCalled();
 await h.handle({type:"webviewReady"},false);expect(target).toHaveBeenCalledOnce();expect(switchThread).toHaveBeenCalledWith("B2");}finally{await h.dispose();}
});
it("registry-full actual open flow never retains hidden hosts, repeated refusals preserve existing slots",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"cp9-full-"));dirs.push(dir);const root=join(dir,"new");mkdirSync(root);const file=join(dir,"registry.json");writeFileSync(file,JSON.stringify({version:1,projects:Array.from({length:32},(_,i)=>{const path=join(dir,"old"+i);return{id:projectId(path),root:path,name:"old"+i,openedAt:0};})}));const registry=new ProjectRegistry(file),pool=new ProjectPool<any>(),notices:string[]=[];
 const source=readFileSync(new URL("../src/main.ts",import.meta.url),"utf8"),original=source.slice(source.indexOf("async function openProject("),source.indexOf("async function selectRecent("));const code=transformSync(original,{loader:"ts",target:"es2022"}).code;
 const createProjectHost=vi.fn(()=>({canDetach:()=>true,detach(){},flush(){},activity:async()=>({state:"idle",attention:[]}),pause:async()=>true,dispose:async()=>{}}));const existing=createProjectHost();pool.add("kept",existing);const deps={provisioner:{busy:false,install:vi.fn()},window:{},pool,registry,app:{getPath:()=>dir,isPackaged:true},dialog:{},realpath,stat,projectId,mkdirSync,appendFileSync:()=>{},join,homedir,createNodeProcessDeps:()=>({}),assertProjectAvailable:async()=>{},randomUUID:()=>"uuid",createProjectHost,safeStorage:{},persistence:()=>false,slots:new Map(),notice:async(message:string)=>{notices.push(message);},publishProjects:async()=>{},flushDraft:async()=>{},attachProject:async()=>{},__dirname:dir};
 const run=new Function("d",`let opening=false,closing=false,closed=false,projectOpenGeneration=0,openingLease,host;const {${Object.keys(deps).join(",")}}=d;${code};return openProject;`)(deps);createProjectHost.mockClear();await run(root);await run(root);expect(notices).toHaveLength(2);expect(notices[0]).toContain("Recents is full");expect(createProjectHost).not.toHaveBeenCalled();expect(pool.summaries()).toHaveLength(1);expect(pool.get("kept")).toBe(existing);await pool.dispose();
});

it("same-port restart and superseded trust reads discard old success and failure instead of replacing current access",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"cp9-access-"));dirs.push(dir);const root=join(dir,"root");mkdirSync(root);
 const port=55685,base=`http://127.0.0.1:${port}`,old=newOwner("old",root),next=newOwner("next",root);old.pid=42;next.pid=43;registerOwner(port,old);
 let current=old,release!:(r:Response)=>void;const held=new Promise<Response>(r=>release=r),post=vi.fn();
 const status=(o:typeof old,trusted:boolean)=>({protocol:1,workspace:root,process_id:o===old?"old-process":"next-process",pid:o.pid,client_id:o.clientId,client_name:o.name,role:"owner",trusted});
 const fetch=vi.fn(async(url:string,init?:RequestInit)=>{if(url.includes("/health?")){const nonce=new URL(url).searchParams.get("nonce")!;return new Response(JSON.stringify({pid:current.pid,proof:healthProof("token",nonce),bound:healthBound("token",nonce,current.pid,root)}));}return new Headers(init?.headers).get("x-crucible-owner")===old.key?held:new Response(JSON.stringify(status(next,false)));});vi.stubGlobal("fetch",fetch);
 const h=fixture({root,post});vi.spyOn(h.runtime,"backendUrl").mockReturnValue(base);
 try{(h.runtime as any).readyListeners[0](root);await vi.waitFor(()=>expect(fetch.mock.calls.some(c=>c[0].endsWith("/project/session"))).toBe(true));
 current=next;registerOwner(port,next);(h.runtime as any).readyListeners[0](root);await vi.waitFor(()=>expect(post).toHaveBeenCalledWith(expect.objectContaining({type:"desktop/access",pid:43,trusted:false})));post.mockClear();
 release(new Response(JSON.stringify(status(old,true))));await new Promise(r=>setTimeout(r,0));expect(post.mock.calls.map(c=>c[0])).not.toContainEqual(expect.objectContaining({type:"desktop/access",pid:42}));expect(post.mock.calls.map(c=>c[0])).not.toContainEqual(expect.objectContaining({type:"desktop/access",state:"unavailable"}));
 }finally{release(new Response("{}"));await h.dispose();forgetOwner(port,old);forgetOwner(port,next);}
});

it.each(["assert","registry","flush"])("an actual open held at %s is retired by quit and cannot revive after Cancel",async boundary=>{
 const source=readFileSync(new URL("../src/main.ts",import.meta.url),"utf8"),block=source.slice(source.indexOf("async function openProject("),source.indexOf("async function selectRecent(")),code=transformSync(block,{loader:"ts",target:"es2022"}).code;
 let release!:()=>void,reached!:()=>void;const held=new Promise<void>(r=>release=r),entered=new Promise<void>(r=>reached=r),start=vi.fn(async()=>{}),dispose=vi.fn(async()=>{}),pool=new ProjectPool<any>(),created=vi.fn(()=>({canDetach:()=>true,detach(){},flush(){},activity:async()=>({state:"idle",attention:[]}),pause:async()=>true,dispose,start}));
 const gate=async(name:string)=>{if(name===boundary){reached();await held;}};
 const deps={provisioner:{busy:false,install:vi.fn()},window:{isDestroyed:()=>false},pool,registry:{add:async(root:string)=>{await gate("registry");return{root,id:"p",name:"fixture"};}},app:{getPath:()=>"/private/tmp/unused",isPackaged:true},dialog:{},realpath:async(x:string)=>x,stat:async()=>({isDirectory:()=>true}),projectId:()=>"p",mkdirSync:vi.fn(),appendFileSync:vi.fn(),join,homedir:()=>"/private/tmp",createNodeProcessDeps:()=>({}),assertProjectAvailable:async()=>gate("assert"),randomUUID:()=>"v",createProjectHost:created,safeStorage:{},persistence:()=>false,slots:new Map(),notice:vi.fn(),publishProjects:vi.fn(async()=>{}),openExternal:vi.fn(),theme:()=>({appearance:"dark",accent:"blue"}),flushDraft:async()=>gate("flush"),attachProject:async(_e:unknown,h:any)=>h.start(),__dirname:"/private/tmp"};
 const run=new Function("d",`let opening=false,closing=false,closed=false,projectOpenGeneration=0,openingLease,host;const {${Object.keys(deps).join(",")}}=d;${code};return{openProject,fence:()=>{closing=true;projectOpenGeneration++;openingLease=undefined;opening=false;},cancel:()=>closing=false};`)(deps);
 deps.provisioner.busy=true;await run.openProject("/private/tmp/disposable");expect(created).not.toHaveBeenCalled();expect(deps.notice).toHaveBeenCalledOnce();deps.provisioner.busy=false;deps.notice.mockClear();
 const opening=run.openProject("/private/tmp/disposable");await entered;run.fence();await pool.dispose();run.cancel();release();await opening;expect(start).not.toHaveBeenCalled();expect(deps.notice).not.toHaveBeenCalled();if(boundary!=="flush")expect(created).not.toHaveBeenCalled();
 // Ordinary open is still allowed after the cancelled admission has retired.
 await run.openProject("/private/tmp/disposable");expect(start).toHaveBeenCalledOnce();await pool.dispose();
});
