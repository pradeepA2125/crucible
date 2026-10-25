import{afterEach,expect,it,vi}from"vitest";import{mkdtempSync,mkdirSync,rmSync}from"node:fs";import{tmpdir}from"node:os";import{join}from"node:path";
import{createProjectHost}from"../src/host.js";import{CrucibleController}from"../../vscode-extension/src/controller.js";
const dirs:string[]=[];afterEach(()=>{vi.restoreAllMocks();dirs.splice(0).forEach(d=>rmSync(d,{recursive:true,force:true}));});
it("unconfigured Setup host closes twice without acquiring backend configuration, and runtime cleanup survives local teardown failure",async()=>{
 const profile=mkdtempSync(join(tmpdir(),"cp11-cleanup-"));dirs.push(profile);const root=join(profile,"root");mkdirSync(root);const setup=vi.fn();
 const host=createProjectHost({root,profile,resources:profile,runtimeDir:join(profile,"runtime"),draftPersistence:()=>false,encryption:{isEncryptionAvailable:()=>false,encryptString:()=>Buffer.alloc(0),decryptString:()=>""},post:vi.fn(),viewIdentity:()=>1,isOpen:()=>true,showChat(){},showSetup:setup,showSettings(){},openExternal:async()=>{},revealFile:async()=>{},readTheme:()=>({appearance:"dark",accent:"blue"}),saveTheme:async()=>{},log(){},backendReady(){}});
 await host.start();expect(setup).toHaveBeenCalledOnce();const dispose=vi.spyOn(host.runtime,"dispose");
 const controller=vi.spyOn(CrucibleController.prototype,"dispose").mockImplementationOnce(()=>{throw Error("controller failure");});
 await expect(host.dispose()).rejects.toThrow("Project cleanup failed");expect(dispose).toHaveBeenCalledOnce();controller.mockRestore();await expect(host.dispose()).resolves.toBeUndefined();
 await expect(host.handle({type:"newChat"},false)).rejects.toThrow("closing");
});

import{readFileSync}from"node:fs";import{transformSync}from"esbuild";import{cleanAll,quitFingerprint}from"../src/lifecycle.js";
function quitHarness(){
 const source=readFileSync(new URL("../src/main.ts",import.meta.url),"utf8"),block=source.slice(source.indexOf("async function previewQuit("),source.indexOf("const flushDraft =")),code=transformSync(block,{loader:"ts",target:"es2022"}).code;
 let current:any[]=[];const shown:any[]=[],window={hide:vi.fn(),show:vi.fn(),focus:vi.fn()},app={quit:vi.fn(),hide:vi.fn()},pool={refresh:vi.fn(async()=>current),flush:vi.fn(),dispose:vi.fn(async()=>{})},draftFlusher={beforeQuit:vi.fn(async()=>{})},terminals={dispose:vi.fn(async()=>{})};
 const d={provisioner:{busy:false,dispose:vi.fn(async()=>{})},windowMemory:{flush:vi.fn(async()=>{})},observations:(values:any[])=>values,pool,quitFingerprint,randomUUID:()=>String(Math.random()),restoreMainWindow:()=>{window.show();window.focus();},lifecycleWindow:{show:vi.fn(async(p:any)=>{shown.push(p);}),close:vi.fn()},Date,window,draftFlusher,post:vi.fn(),context:{viewId:"v"},releaseAwake:vi.fn(),clearInterval:vi.fn(),cleanAll,disposeReaders:vi.fn(),terminals,notices:new Map(),app,console};
 const run=new Function("d",`let updates,installAfterCleanup,pendingUpdate;let closing=true,closed=false,finishing=false,cleanupStarted=false,discardApproved=false,systemShutdown=false,draftReady=true,rendererAlive=true,previewFingerprint="",previewAt=0,projectsTimer;const process={platform:"linux"};const {${Object.keys(d).join(",")}}=d;${code};return{previewQuit,finishQuit,quitChoice,status:()=>({closing,closed,finishing})};`)(d);
 return{...d,run,shown,setCurrent:(value:any[])=>current=value};
}
it("actual quit coordinator preserves Cancel/Keep running and coalesces final cleanup",async()=>{
 const h=quitHarness();await h.run.previewQuit();await h.run.quitChoice("cancel",h.shown.at(-1));expect(h.window.hide).not.toHaveBeenCalled();expect(h.run.status().closing).toBe(false);expect(h.app.quit).not.toHaveBeenCalled();
 const b=quitHarness();await b.run.previewQuit();await b.run.quitChoice("background",b.shown.at(-1));expect(b.window.hide).toHaveBeenCalledOnce();expect(b.pool.dispose).not.toHaveBeenCalled();expect(b.draftFlusher.beforeQuit).not.toHaveBeenCalled();
 const q=quitHarness();await q.run.previewQuit();await Promise.all([q.run.finishQuit(),q.run.finishQuit()]);expect(q.pool.dispose).toHaveBeenCalledOnce();expect(q.terminals.dispose).toHaveBeenCalledOnce();expect(q.app.quit).toHaveBeenCalledOnce();
});
it("actual quit coordinator revalidates changed work and requires explicit discard after save failure",async()=>{
 const h=quitHarness();await h.run.previewQuit();h.setCurrent([{id:"p",name:"Alpha",state:"busy",owner:true,identity:"owner",shells:[{id:"s",label:"zsh"}],threads:[]}]);await h.run.quitChoice("quit",h.shown[0]);expect(h.shown.at(-1).error).toContain("changed");expect(h.pool.dispose).not.toHaveBeenCalled();
 h.draftFlusher.beforeQuit.mockRejectedValue(Error("Save failed"));await h.run.quitChoice("quit",h.shown.at(-1));expect(h.shown.at(-1).phase).toBe("draft-error");await h.run.quitChoice("quit",h.shown.at(-1));expect(h.draftFlusher.beforeQuit).toHaveBeenCalledTimes(2);expect(h.app.quit).not.toHaveBeenCalled();
 await h.run.quitChoice("discard",h.shown.at(-1));expect(h.app.quit).toHaveBeenCalledOnce();expect(h.draftFlusher.beforeQuit).toHaveBeenCalledTimes(2);
});
it("actual cleanup failure still stops other resources and leaves only an explicit retry",async()=>{
 const h=quitHarness();h.pool.dispose.mockRejectedValueOnce(Error("backend alive"));await h.run.previewQuit();await h.run.quitChoice("quit",h.shown.at(-1));expect(h.terminals.dispose).toHaveBeenCalledOnce();expect(h.shown.at(-1).phase).toBe("cleanup-error");expect(h.app.quit).not.toHaveBeenCalled();
 await expect(h.run.quitChoice("cancel",h.shown.at(-1))).rejects.toThrow("already begun");await h.run.quitChoice("quit",h.shown.at(-1));expect(h.app.quit).toHaveBeenCalledOnce();
});
it("actual OS-shutdown handler retains its grace deadline even when cleanup is already pending",async()=>{
 vi.useFakeTimers();try{
 const source=readFileSync(new URL("../src/main.ts",import.meta.url),"utf8"),start=source.indexOf('    // Electron 45 declarations'),end=source.indexOf('    meta = new JsonState',start),code=transformSync(source.slice(start,end),{loader:"ts",target:"es2022"}).code;
 const handlers=new Map<string,Function>(),d={powerMonitor:{on:(name:string,fn:Function)=>handlers.set(name,fn)},lifecycleWindow:{close:vi.fn()},releaseAwake:vi.fn(),finishQuit:vi.fn(()=>new Promise(()=>{})),app:{quit:vi.fn()},console:{error:vi.fn()},transitions:{reset:vi.fn()},pool:{refresh:vi.fn(async()=>[])},updateLifecycle:vi.fn(),setTimeout};
 new Function("d",`let systemShutdown=false,closing=false,closed=false,suspended=false,projectOpenGeneration=0,openingLease,opening=false;const {${Object.keys(d).join(",")}}=d;${code}`)(d);
 const event={preventDefault:vi.fn()};handlers.get("shutdown")!(event);expect(event.preventDefault).toHaveBeenCalledOnce();expect(d.app.quit).not.toHaveBeenCalled();await vi.advanceTimersByTimeAsync(16001);expect(d.app.quit).toHaveBeenCalledOnce();expect(d.console.error).toHaveBeenCalledWith(expect.stringContaining("could not be verified"));
 }finally{vi.useRealTimers();}
});

import{DraftVault}from"../src/draft-vault.js";
it("actual encrypted host preserves exact drafts and running work across a failed quit preflight and later retry",async()=>{
 const profile=mkdtempSync(join(tmpdir(),"cp11-vault-retry-"));dirs.push(profile);const root=join(profile,"root");mkdirSync(root);let fail=false;
 const encryption={isEncryptionAvailable:()=>true,encryptString:(text:string)=>{if(fail)throw Error("synthetic keychain locked");return Buffer.from(text);},decryptString:(bytes:Buffer)=>bytes.toString()};const post=vi.fn();
 const host=createProjectHost({root,profile,resources:profile,runtimeDir:join(profile,"runtime"),draftPersistence:()=>true,encryption,post,viewIdentity:()=>1,isOpen:()=>true,showChat(){},showSetup(){},showSettings(){},openExternal:async()=>{},revealFile:async()=>{},readTheme:()=>({appearance:"dark",accent:"blue"}),saveTheme:async()=>{},log(){},backendReady(){}});
 const runtime=vi.spyOn(host.runtime,"dispose");
 try{
  await host.handle({type:"draft/read",requestId:"read",threadId:"new"},false);
  await host.handle({type:"draft/save",requestId:"save",threadId:"new",revision:1,version:0,draft:{text:"KEEP EXACT SYNTHETIC DRAFT",imageAttachments:[],mentionedPaths:[]}},false);
  fail=true;expect(()=>host.flush()).toThrow("keychain");await expect(host.dispose()).rejects.toThrow("draft cleanup");expect(runtime).not.toHaveBeenCalled();
  await host.handle({type:"draft/read",requestId:"retry-read",threadId:"new"},false);expect(post.mock.calls.at(-1)?.[0].snapshot.draft.text).toBe("KEEP EXACT SYNTHETIC DRAFT");
  fail=false;host.flush();const saved=new DraftVault(join(profile,"drafts"),host.projectId,encryption,()=>true).read() as any;expect(saved.entries[0].draft.text).toBe("KEEP EXACT SYNTHETIC DRAFT");await host.dispose();expect(runtime).toHaveBeenCalledOnce();
 }finally{fail=false;await host.dispose();}
});
it("explicit discard bypasses only unsaved vault persistence, preserving its last saved copy through cleanup retries",async()=>{
 const h=quitHarness();h.pool.flush.mockImplementation(()=>{throw Error("keychain locked");});await h.run.previewQuit();await h.run.quitChoice("quit",h.shown.at(-1));expect(h.shown.at(-1).phase).toBe("draft-error");expect(h.pool.dispose).not.toHaveBeenCalled();expect(h.terminals.dispose).not.toHaveBeenCalled();
 h.pool.dispose.mockRejectedValueOnce(Error("runtime alive"));await h.run.quitChoice("discard",h.shown.at(-1));expect(h.pool.dispose).toHaveBeenLastCalledWith(true);expect(h.shown.at(-1).phase).toBe("cleanup-error");await h.run.quitChoice("quit",h.shown.at(-1));expect(h.pool.dispose).toHaveBeenLastCalledWith(true);expect(h.pool.flush).toHaveBeenCalledOnce();expect(h.draftFlusher.beforeQuit).toHaveBeenCalledOnce();expect(h.app.quit).toHaveBeenCalledOnce();
});

it("actual host explicit discard preserves previous ciphertext while the keychain remains unavailable",async()=>{
 const profile=mkdtempSync(join(tmpdir(),"cp11-host-discard-"));dirs.push(profile);const root=join(profile,"root");mkdirSync(root);let locked=false;
 const encryption={isEncryptionAvailable:()=>true,encryptString:(raw:string)=>{if(locked)throw Error("synthetic locked keychain");return Buffer.from(raw);},decryptString:(bytes:Buffer)=>bytes.toString()};
 const host=createProjectHost({root,profile,resources:profile,runtimeDir:join(profile,"runtime"),draftPersistence:()=>true,encryption,post:vi.fn(),viewIdentity:()=>1,isOpen:()=>true,showChat(){},showSetup(){},showSettings(){},openExternal:async()=>{},revealFile:async()=>{},readTheme:()=>({appearance:"dark",accent:"blue"}),saveTheme:async()=>{},log(){},backendReady(){}});
 await host.handle({type:"draft/read",requestId:"r",threadId:"new"},false);await host.handle({type:"draft/save",requestId:"s",threadId:"new",revision:1,version:0,draft:{text:"last saved copy",imageAttachments:[],mentionedPaths:[]}},false);host.flush();
 await host.handle({type:"draft/save",requestId:"latest",threadId:"new",revision:2,version:0,draft:{text:"explicitly discarded latest edit",imageAttachments:[],mentionedPaths:[]}},false);locked=true;expect(()=>host.flush()).toThrow("locked");await expect(host.dispose(true)).resolves.toBeUndefined();
 const saved=new DraftVault(join(profile,"drafts"),host.projectId,encryption,()=>true).read() as any;expect(saved.entries[0].draft.text).toBe("last saved copy");
});

it("actual quit preview names installer cancellation and waits for it before quitting",async()=>{const h=quitHarness();h.provisioner.busy=true;await h.run.previewQuit();expect(h.shown.at(-1).error).toMatch(/cancels installation/);let release!:()=>void;const held=new Promise<void>(r=>release=r);h.provisioner.dispose.mockImplementationOnce(async()=>held);const quitting=h.run.finishQuit();await vi.waitFor(()=>expect(h.provisioner.dispose).toHaveBeenCalledOnce());expect(h.app.quit).not.toHaveBeenCalled();release();await quitting;expect(h.windowMemory.flush).toHaveBeenCalledOnce();expect(h.app.quit).toHaveBeenCalledOnce();});
