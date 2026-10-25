import { afterEach, expect, it, vi } from "vitest";
import { Inspectors, type InspectorWindow } from "../src/inspectors.js";
import type { BridgeContext } from "../src/contract.js";
import type { MemoryDataSource } from "../../vscode-extension/src/memory-data.js";
import type { GraphHostDeps } from "../../vscode-extension/src/graph/graph-messages.js";
const context: BridgeContext = { v:1, clientId:"client", viewId:"main", projectId:"project", threadId:"a", processId:"process" };
function fixture() {
  const windows: any[] = [];
  const manager = new Inspectors(kind => {
    const handlers = new Map<string,Function>(), frame = { url: `app://app/${kind}.html` };
    const win = { webContents: { id: windows.length+1, mainFrame:frame, getURL:()=>frame.url,send:vi.fn(),on:(key:string,fn:Function)=>handlers.set(key,fn) },
      handlers, destroyed:false,isDestroyed(){return this.destroyed;},focus:vi.fn(),show:vi.fn(),close(){this.destroyed=true;handlers.get("closed")?.();},loadURL:vi.fn(async()=>{}),on:(key:string,fn:Function)=>handlers.set(key,fn) };
    windows.push(win); return win as InspectorWindow;
  },()=>({type:"themeChanged",host:"desktop",appearance:"dark",accent:"blue"}));
  const memory: MemoryDataSource = {getInspect:vi.fn(async()=>null),browse:vi.fn(async()=>[]),getChain:vi.fn(async()=>[])};
  const graph = {loadModel:vi.fn(()=>({stars:[]})),staleAgeSec:()=>null,fileDetail:vi.fn(),symbolDetail:vi.fn(),searchSymbols:vi.fn(()=>[]),openFile:vi.fn(async()=>{}),buildIndex:vi.fn(async()=>{})} as unknown as GraphHostDeps;
  const event=(i=0)=>({sender:{id:windows[i].webContents.id},senderFrame:windows[i].webContents.mainFrame});
  const request=(payload:unknown,sequence=1,i=0)=>({...manager.bootstrap(event(i)),requestId:`req-${sequence}`,sequence,payload});
  return {manager,memory,graph,windows,event,request};
}
afterEach(()=>vi.useRealTimers());
it("runs original handlers with captured thread and denies chat execution, foreign frames and wrong documents",async()=>{
 const f=fixture();await f.manager.open("memory",context,"/project",f.memory,f.graph);
 await expect(f.manager.handle(f.event(),f.request({type:"ready"}))).resolves.toEqual({});expect(f.memory.getInspect).toHaveBeenCalledWith("a");
 expect(f.windows[0].webContents.send).toHaveBeenCalledWith("crucible:event",expect.objectContaining({payload:{type:"list",memories:[]}}));
 await expect(f.manager.handle(f.event(),f.request({type:"sendMessage",text:"never"},2))).resolves.toMatchObject({error:expect.stringContaining("unavailable")});
 const foreign={sender:{id:1},senderFrame:{url:"app://app/memory.html"}};expect(()=>f.manager.bootstrap(foreign)).toThrow("Untrusted");
 f.windows[0].webContents.mainFrame.url="app://app/settings.html";expect(()=>f.manager.bootstrap(f.event())).toThrow("Wrong inspector");
});
it("rejects stale envelopes and keeps actual work leases after timeout and window closure",async()=>{
 vi.useFakeTimers();const f=fixture();let release!:(v:null)=>void;f.memory.getInspect=vi.fn(()=>new Promise<null>(r=>release=r));await f.manager.open("memory",context,"/project",f.memory,f.graph);
 const pending=f.manager.handle(f.event(),f.request({type:"ready"}));await vi.advanceTimersByTimeAsync(15001);await expect(pending).resolves.toMatchObject({error:expect.stringContaining("timed out")});
 await expect(f.manager.handle(f.event(),f.request({type:"refresh"},2))).resolves.toMatchObject({error:expect.stringContaining("still reading")});
 f.windows[0].close();const sends=f.windows[0].webContents.send.mock.calls.length;release(null);await Promise.resolve();await Promise.resolve();expect(f.windows[0].webContents.send).toHaveBeenCalledTimes(sends);
 await f.manager.open("graph",context,"/project",f.memory,f.graph);const stale={...f.request({type:"ready"},1,1),threadId:"b"};await expect(f.manager.handle(f.event(1),stale)).resolves.toMatchObject({error:expect.stringContaining("Stale")});
 f.manager.dispose();
});
it("keeps memory/graph protocols separate and reuses only an inspector for the same thread",async()=>{
 const f=fixture();await f.manager.open("graph",context,"/project",f.memory,f.graph);await f.manager.open("graph",context,"/project",f.memory,f.graph);expect(f.windows).toHaveLength(1);expect(f.windows[0].focus).toHaveBeenCalledOnce();
 await expect(f.manager.handle(f.event(),f.request({type:"loadChain",memoryId:"private"}))).resolves.toMatchObject({error:expect.any(String)});expect(f.memory.getChain).not.toHaveBeenCalled();
 await f.manager.handle(f.event(),f.request({type:"ready"},2));expect(f.graph.loadModel).toHaveBeenCalledOnce();f.manager.broadcastTheme();expect(f.windows[0].webContents.send).toHaveBeenLastCalledWith("crucible:event",expect.objectContaining({payload:expect.objectContaining({host:"desktop"})}));
});

it("hydrates the latest reloaded document after an old read settles, without publishing its stale deadline",async()=>{
 vi.useFakeTimers();const f=fixture();let release!:(v:null)=>void;let calls=0;
 f.memory.getInspect=vi.fn(()=>++calls===1?new Promise<null>(r=>release=r):Promise.resolve(null));
 await f.manager.open("memory",context,"/project",f.memory,f.graph);
 const first=f.manager.handle(f.event(),f.request({type:"ready"}));const old=f.manager.bootstrap(f.event()).viewId;
 f.windows[0].handlers.get("did-start-navigation")({},"app://app/memory.html",false,true);
 const next=f.manager.bootstrap(f.event()).viewId;expect(next).not.toBe(old);
 await f.manager.handle(f.event(),f.request({type:"ready"}));await f.manager.handle(f.event(),f.request({type:"ready"},2));
 await vi.advanceTimersByTimeAsync(15001);await first;
 expect(f.windows[0].webContents.send.mock.calls.filter(([,m]:any)=>m.context.viewId===next)).toHaveLength(0);
 release(null);await vi.advanceTimersByTimeAsync(0);expect(f.memory.getInspect).toHaveBeenCalledTimes(2);
 expect(f.windows[0].webContents.send).toHaveBeenCalledWith("crucible:event",expect.objectContaining({context:expect.objectContaining({viewId:next}),payload:{type:"trace",trace:null}}));
 f.manager.dispose();
});
