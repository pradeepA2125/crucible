import{afterEach,expect,it,vi}from"vitest";import{mkdtempSync,readFileSync,writeFileSync,rmSync,readdirSync,statSync}from"node:fs";import{tmpdir}from"node:os";import{join}from"node:path";import{createCipheriv,createDecipheriv,randomBytes}from"node:crypto";
import{DraftVault}from"../src/draft-vault.js";import{DraftStore}from"../../vscode-extension/src/drafts/draft-store.js";import{DraftSession}from"../../vscode-extension/src/drafts/draft-session.js";
const dirs:string[]=[];afterEach(()=>{vi.useRealTimers();dirs.splice(0).forEach(d=>rmSync(d,{recursive:true,force:true}));});
const key=randomBytes(32);const encryption={isEncryptionAvailable:()=>true,getSelectedStorageBackend:()=>"keychain",encryptString:(text:string)=>{const iv=randomBytes(12),c=createCipheriv("aes-256-gcm",key,iv);const bytes=Buffer.concat([c.update(text,"utf8"),c.final()]);return Buffer.concat([iv,c.getAuthTag(),bytes]);},decryptString:(b:Buffer)=>{const c=createDecipheriv("aes-256-gcm",key,b.subarray(0,12));c.setAuthTag(b.subarray(12,28));return Buffer.concat([c.update(b.subarray(28)),c.final()]).toString("utf8");}};
function setup(){const dir=mkdtempSync(join(tmpdir(),"cp9-vault-"));dirs.push(dir);let enabled=false;const id="a".repeat(64),vault=new DraftVault(dir,id,encryption,()=>enabled);return{dir,id,vault,on:()=>enabled=true,off:()=>enabled=false};}
const draft={text:"private draft",mentionedPaths:["sample.ts"],imageAttachments:[{name:"x.png",mediaType:"image/png",bytes:1,dataUrl:"data:image/png;base64,AA=="}]};
it("off never encrypts, decrypts or writes; opt-in ciphertext restores exact text/image project state",()=>{
 const h=setup(),s=new DraftStore();s.save("t",1,draft);h.vault.changed(()=>s.exportState(),true);expect(readdirSync(h.dir)).toEqual([]);expect(h.vault.read()).toBeUndefined();
 h.on();h.vault.changed(()=>s.exportState(),true);const bytes=readFileSync(join(h.dir,h.id+".draft"));expect(bytes.includes(Buffer.from(draft.text))).toBe(false);expect(statSync(join(h.dir,h.id+".draft")).mode&0o777).toBe(0o600);
 const restored=new DraftStore();restored.importState(h.vault.read());expect(restored.read("t").draft).toEqual(draft);h.off();expect(h.vault.read()).toBeUndefined();DraftVault.remove(h.dir);expect(readdirSync(h.dir)).toEqual([]);
});
it("wrong-root/tampered data and unavailable keychain block overwrite; no plaintext fallback",()=>{
 const h=setup();h.on();const file=join(h.dir,h.id+".draft");writeFileSync(file,encryption.encryptString(JSON.stringify({version:1,projectId:"b".repeat(64),drafts:{}})));
 expect(()=>h.vault.read()).toThrow("different project");const original=readFileSync(file);expect(()=>h.vault.changed(()=>({}),true)).toThrow("kept unchanged");expect(readFileSync(file)).toEqual(original);
 const blocked=new DraftVault(h.dir,"b".repeat(64),{...encryption,getSelectedStorageBackend:()=>"basic_text"},()=>true);expect(()=>blocked.changed(()=>({}),true)).toThrow("keychain");
 const bad=new DraftVault(h.dir,h.id,encryption,()=>true);writeFileSync(file,Buffer.from("broken"));expect(()=>bad.read()).toThrow();expect(readFileSync(file).toString()).toBe("broken");
});
it("durably quarantines pending text before begin reply; restart cannot dispatch or ordinarily resend it",()=>{
 const h=setup();h.on();const post=vi.fn();let session:DraftSession;session=new DraftSession({identity:()=>"v",post,admission:()=>{},changed:urgent=>h.vault.changed(()=>session.exportState(),urgent)});
 session.observe({type:"clearThread",threadId:"t"});session.handle({type:"draft/read",requestId:"r",threadId:"t"});session.handle({type:"draft/begin",requestId:"b",threadId:"t",revision:1,draft,draftId:"pending"});
 const restored=new DraftStore();restored.importState(h.vault.read());expect(restored.read("t").draft.text).toBe("");expect(restored.hasSubmission).toBe(false);expect(restored.hasRecovery).toBe(true);expect(restored.dispatch("pending")).toBe(false);expect(()=>restored.begin("t",2,draft,"retry")).toThrow("quarantine");
 // Only an explicit host-confirmed recovery can release text; it never dispatches.
 restored.recover();expect(restored.read("t").returned?.draft).toEqual(draft);expect(restored.hasSubmission).toBe(false);session.dispose();h.vault.reset();
});
it("a failed urgent save refuses begin before any dispatch, and preserves recoverable text",()=>{
 const h=setup();h.on();const post=vi.fn();let session:DraftSession;const bad=new DraftVault(h.dir,h.id,{...encryption,isEncryptionAvailable:()=>false},()=>true);
 session=new DraftSession({identity:()=>"v",post,admission:()=>{},changed:u=>bad.changed(()=>session.exportState(),u)});session.observe({type:"clearThread",threadId:"t"});session.handle({type:"draft/read",requestId:"r",threadId:"t"});session.handle({type:"draft/begin",requestId:"b",threadId:"t",revision:1,draft,draftId:"p"});
 expect(post.mock.calls.at(-1)?.[0]).toMatchObject({type:"draft/result",requestId:"b",error:expect.stringContaining("keychain")});expect(()=>session.dispatch({type:"sendMessage",draftId:"p"})).toThrow("Prepare");session.handle({type:"draft/read",requestId:"r2",threadId:"t"});expect(post.mock.calls.at(-1)?.[0].snapshot.returned.draft.text).toBe(draft.text);bad.reset();session.dispose();
});

it("disk and restored-entry budgets refuse excess without evicting existing ciphertext",async()=>{
 const {truncateSync}=await import("node:fs");const h=setup();h.on();const s=new DraftStore();s.save("t",1,draft);h.vault.changed(()=>s.exportState(),true);
 const file=join(h.dir,h.id+".draft"),original=readFileSync(file),other=join(h.dir,"b".repeat(64)+".draft");writeFileSync(other,"");truncateSync(other,256*1024*1024);
 expect(()=>h.vault.changed(()=>s.exportState(),true)).toThrow("storage is full");expect(readFileSync(file)).toEqual(original);h.vault.reset();
 const tooMany={version:1,entries:Array.from({length:33},(_,n)=>({threadId:String(n),revision:1,version:0,draft})),tombstones:[]};const restored=new DraftStore();expect(()=>restored.importState(tooMany)).toThrow();expect(restored.read("0").draft.text).toBe("");
});

it("quarantine A blocks all new preparations, remains durable while B edits, and is only released by explicit recovery",()=>{
 const a=new DraftStore();a.save("A",1,draft);a.begin("A",2,draft,"A-id");const restored=new DraftStore();restored.importState(a.exportState());restored.save("B",1,{...draft,text:"B edit"});
 expect(()=>restored.begin("B",2,{...draft,text:"B send"},"B-id")).toThrow("quarantine");const restart=new DraftStore();restart.importState(restored.exportState());expect(restart.hasSubmission).toBe(false);restart.recover();expect(restart.read("A").returned?.draft).toEqual(draft);expect(restart.read("B").draft.text).toBe("B edit");
 restart.begin("B",2,{...draft,text:"B send"},"B-id");restart.reject("B-id");const again=new DraftStore();again.importState(restart.exportState());expect(again.read("A").returned?.draft).toEqual(draft);expect(again.read("B").returned?.draft.text).toBe("B send");
});
it("own crash-left ciphertext counts toward quota and is removed on project/global optout, unrelated files stay",async()=>{
 const {truncateSync}=await import("node:fs"),h=setup();h.on();const orphan=h.id+".draft.12345678-1234-4234-8234-123456789abc.tmp",other="b".repeat(64)+".draft.12345678-1234-4234-8234-123456789abc.tmp";
 writeFileSync(join(h.dir,orphan),"");truncateSync(join(h.dir,orphan),256*1024*1024);writeFileSync(join(h.dir,other),"other ciphertext");writeFileSync(join(h.dir,"unrelated.tmp"),"keep");
 expect(()=>h.vault.changed(()=>({}),true)).toThrow("storage is full");h.vault.reset();DraftVault.remove(h.dir,h.id);expect(readdirSync(h.dir).sort()).toEqual([other,"unrelated.tmp"].sort());DraftVault.remove(h.dir);expect(readdirSync(h.dir)).toEqual(["unrelated.tmp"]);
});

it("freezes failed persistence bytes before a lazy caller is cleared; explicit discard keeps prior ciphertext",()=>{
 const h=setup();h.on();let fail=false,value:any={text:"prior saved"};const e={...encryption,encryptString:(raw:string)=>{if(fail)throw Error("disk locked");return encryption.encryptString(raw);}},v=new DraftVault(h.dir,h.id,e,()=>true);
 v.changed(()=>value,true);const file=join(h.dir,h.id+".draft"),prior=readFileSync(file);
 value={text:"exact latest"};v.changed(()=>value);fail=true;expect(()=>v.flush()).toThrow("locked");value={};fail=false;v.flush();expect(v.read()).toEqual({text:"exact latest"});
 const saved=readFileSync(file);value={text:"explicitly unsaved"};v.changed(()=>value);fail=true;expect(()=>v.flush()).toThrow();v.discardPending();expect(readFileSync(file)).toEqual(saved);expect(readFileSync(file)).not.toEqual(prior);v.reset();
});
