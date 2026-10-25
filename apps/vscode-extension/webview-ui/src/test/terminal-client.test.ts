import{it,expect,vi}from"vitest";import{createTerminalClient}from"../desktop/terminalClient";
const post=(data:unknown)=>window.dispatchEvent(new MessageEvent("message",{data}));
it("correlates strict replies and refuses an old conversation result",async()=>{
 const send=vi.fn(),client=createTerminalClient(send);post({type:"desktop/baselineContext",projectId:"a",viewId:"v",threadId:"t"});
 const reading=client.request({operation:"list"});const request=send.mock.calls[0]![0];post({type:"desktop/terminalResult",requestId:request.requestId,result:{sessions:[]}});await expect(reading).resolves.toEqual({sessions:[]});
 const stale=client.request({operation:"agentList",threadId:"t"});const rejection=expect(stale).rejects.toThrow(/changed/);post({type:"desktop/baselineContext",projectId:"a",viewId:"v",threadId:"other"});await rejection;client.dispose();
});
it("bounds outstanding requests, rejects malformed results and clears on close",async()=>{
 const send=vi.fn(),client=createTerminalClient(send);const invalid=client.request({operation:"list"});post({type:"desktop/terminalResult",requestId:send.mock.calls[0]![0].requestId,result:{data:"x".repeat(90001)}});await expect(invalid).rejects.toThrow(/invalid/);
 const pending=Array.from({length:8},()=>client.request({operation:"list"}).catch(e=>e.message));await expect(client.request({operation:"list"})).rejects.toThrow(/busy/);client.dispose();expect(await Promise.all(pending)).toEqual(Array(8).fill("Terminal view closed."));
});
