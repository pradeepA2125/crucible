import{expect,it,vi}from"vitest";import{LifecycleWindow}from"../src/lifecycle-window.js";import{CHANNEL}from"../src/contract.js";import{randomUUID}from"node:crypto";
function fixture(){const frame={url:"app://app/lifecycle.html"},sent:any[]=[],listeners=new Map<string,Function>();const w={webContents:{id:8,mainFrame:frame,send:(_c:string,m:unknown)=>sent.push(m)},isDestroyed:()=>false,loadURL:vi.fn(async()=>{}),show:vi.fn(),focus:vi.fn(),destroy:vi.fn(),on:(name:string,fn:Function)=>listeners.set(name,fn)};const choose=vi.fn(async(_choice:string,_preview:unknown)=>{}),modal=new LifecycleWindow(()=>w,()=>({type:"themeChanged",host:"desktop"}),choose);return{modal,w,frame,sent,listeners,choose,event:{sender:{id:8},senderFrame:frame}};}
it("gives only the dedicated main frame nonce-bound quit authority, never general project privileges",async()=>{
 const h=fixture(),preview={id:randomUUID(),projects:[],phase:"preview" as const};await h.modal.show(preview);const context=h.modal.bootstrap(h.event);expect(context.projectId).toBeNull();expect(context.threadId).toBeNull();let sequence=0;
 const send=(payload:unknown)=>h.modal.handle(h.event,{...context,requestId:randomUUID(),sequence:++sequence,payload});
 expect((await send({type:"newChat"})).error).toContain("only controls");expect(h.choose).not.toHaveBeenCalled();
 expect((await send({type:"desktop/lifecycleChoice",snapshotId:randomUUID(),choice:"quit"})).error).toContain("changed");
 expect(()=>h.modal.bootstrap({...h.event,senderFrame:{url:"app://app/lifecycle.html"}})).toThrow("Untrusted");
 await send({type:"desktop/lifecycleReady"});expect(h.sent.some(m=>m.payload.type==="desktop/quitPreview")).toBe(true);
 await send({type:"desktop/lifecycleChoice",snapshotId:preview.id,choice:"cancel"});expect(h.choose).toHaveBeenCalledOnce();
 await send({type:"desktop/lifecycleChoice",snapshotId:preview.id,choice:"quit"});expect(h.choose).toHaveBeenCalledOnce();h.modal.close();expect(h.w.destroy).toHaveBeenCalledOnce();
});
it("replaces immutable snapshots and allows recovery without reusing stale answers",async()=>{
 const h=fixture(),preview={id:randomUUID(),projects:[],phase:"draft-error" as const};await h.modal.show(preview);preview.phase="preview" as never;await h.modal.handle(h.event,{...h.modal.bootstrap(h.event),requestId:randomUUID(),sequence:1,payload:{type:"desktop/lifecycleChoice",snapshotId:preview.id,choice:"discard"}});expect(h.choose.mock.calls[0]?.[1]).toMatchObject({phase:"draft-error"});
 const fresh={...preview,id:randomUUID()};await h.modal.show(fresh);await h.modal.handle(h.event,{...h.modal.bootstrap(h.event),requestId:randomUUID(),sequence:2,payload:{type:"desktop/lifecycleChoice",snapshotId:fresh.id,choice:"background"}});expect(h.choose).toHaveBeenCalledTimes(2);
});
