import{expect,it,vi}from"vitest";import{CrucibleController}from"../src/controller.js";import{RuntimeCore}from"../src/runtime/runtime-core.js";
it("real unconfigured controller disposal never reads a backend URL and still attempts later cleanups after errors",()=>{
 const getter=vi.fn(()=>{throw Error("Run Setup first");});
 const c=new CrucibleController(vi.fn() as never,{load:async()=>null,save:async()=>{},clear:async()=>{}} as never,{getBackendBaseUrl:getter} as never,{} as never,{} as never);
 const agent=vi.spyOn((c as any).agentViews,"closeAll").mockImplementation(()=>{throw Error("agent window");});
 const team=vi.spyOn((c as any).teamViews,"closeAll");expect(()=>c.dispose()).toThrow("resources");expect(getter).not.toHaveBeenCalled();expect(agent).toHaveBeenCalled();expect(team).toHaveBeenCalled();
 agent.mockRestore();expect(()=>c.dispose()).not.toThrow();expect(getter).not.toHaveBeenCalled();
});
it("runtime disposal stops every owned managed process and retains only failures for retry",async()=>{
 const core=Object.create(RuntimeCore.prototype) as RuntimeCore;
 const bad={stop:vi.fn().mockRejectedValueOnce(Error("still alive")).mockResolvedValue(undefined)},good={stop:vi.fn().mockResolvedValue(undefined)};
 Object.assign(core,{processes:new Map([["bad",bad],["good",good]]),ports:new Map([["bad",1],["good",2]]),intentionalStops:new Set()});
 await expect(core.dispose()).rejects.toThrow("owned runtimes");expect(good.stop).toHaveBeenCalledOnce();expect((core as any).processes.size).toBe(1);
 await expect(core.dispose()).resolves.toBeUndefined();expect(bad.stop).toHaveBeenCalledTimes(2);expect(good.stop).toHaveBeenCalledOnce();
 await expect(core.startForWorkspace("later")).rejects.toThrow("disposed");
});
