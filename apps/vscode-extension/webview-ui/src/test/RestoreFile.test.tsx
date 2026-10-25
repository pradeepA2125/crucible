import {cleanup,fireEvent,render,screen} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {RestoreFile,FileRecoveryHistory} from "../desktop/RestoreFile";
import {createRestoreClient} from "../desktop/restoreClient";
import type {RestoreResult} from "../../../../desktop/src/restore-protocol";
const token="12545678-1234-4234-8234-123456789abc";
const preview={kind:"preview" as const,token,path:"sample.txt",action:"restore" as const,effect:"replace" as const,currentBytes:5,targetBytes:3};
const action={operation:"undo" as const,receipt:token};
afterEach(()=>{cleanup();vi.useRealTimers();});
it("requires fresh preview and explicit confirmation, initially focuses Cancel, Escape preserves the file",async()=>{
 const run=vi.fn().mockResolvedValue(preview),changed=vi.fn();render(<RestoreFile run={run} action={action} path="sample.txt" onChanged={changed}/>);
 fireEvent.click(screen.getByRole("button",{name:"Restore file…"}));await screen.findByRole("dialog");expect(screen.getByRole("button",{name:"Cancel"})).toHaveFocus();expect(run).toHaveBeenCalledTimes(1);
 expect(screen.getByText(/external editors/)).toBeInTheDocument();fireEvent.keyDown(document,{key:"Escape"});expect(screen.queryByRole("dialog")).toBeNull();expect(screen.getByRole("button",{name:"Restore file…"})).toHaveFocus();expect(changed).not.toHaveBeenCalled();expect(run).toHaveBeenCalledTimes(1);
});
it("applies only returned token and reports a stale-content refusal without another mutation",async()=>{
 const run=vi.fn().mockResolvedValueOnce(preview).mockRejectedValueOnce(new Error("File changed. Refresh."));render(<RestoreFile run={run} action={action} onChanged={vi.fn()}/>);
 fireEvent.click(screen.getByRole("button",{name:"Restore file…"}));await screen.findByRole("dialog");fireEvent.click(screen.getByRole("button",{name:"Restore file"}));await screen.findByText("File changed. Refresh.");expect(run).toHaveBeenLastCalledWith({operation:"apply",token});expect(screen.queryByRole("dialog")).toBeNull();expect(run).toHaveBeenCalledTimes(2);
});
it("offers persistent recovery history and last-result Undo after a diff refresh",async()=>{
 const run=vi.fn().mockResolvedValue({kind:"history",entries:[{id:token,path:"sample.txt",action:"restore",state:"prepared",created:"2026-10-24T00:00:00Z"}]} as RestoreResult);
 render(<FileRecoveryHistory run={run} latest={{kind:"applied",receipt:token,path:"sample.txt",action:"restore"}} onChanged={vi.fn()}/>);expect(screen.getByRole("button",{name:"Undo last restore…"})).toBeEnabled();fireEvent.click(screen.getByText("File recovery history"));fireEvent.click(screen.getByRole("button",{name:"Refresh recovery history"}));await screen.findByText(/Completion unconfirmed/);expect(run).toHaveBeenCalledWith({operation:"history"});expect(screen.getByRole("button",{name:"Preview Undo…"})).toBeEnabled();
});
it("keeps a timed-out physical request occupied and rejects foreign or stale-context success",async()=>{
 vi.useFakeTimers();const send=vi.fn();let identity={viewId:"v",projectId:"p",threadId:"t"};const client=createRestoreClient(send,window,()=>identity);
 const pending=client.run({operation:"apply",token}),refused=expect(pending).rejects.toThrow("not confirmed");await vi.advanceTimersByTimeAsync(45000);await refused;await expect(client.run({operation:"history"})).rejects.toThrow("still finishing");
 const data={type:"desktop/restoreResult",requestId:send.mock.calls[0][0].requestId,...identity,result:{kind:"applied",receipt:token,path:"sample.txt",action:"restore"}};
 window.dispatchEvent(new MessageEvent("message",{source:window,origin:"https://foreign.example",data}));await expect(client.run({operation:"history"})).rejects.toThrow("still finishing");
 window.dispatchEvent(new MessageEvent("message",{source:window,origin:"app://app",data}));
 const next=client.run({operation:"history"}),stale=expect(next).rejects.toThrow("conversation changed");identity={...identity,threadId:"other"};window.dispatchEvent(new MessageEvent("message",{source:window,origin:"app://app",data:{...data,requestId:send.mock.calls[1][0].requestId,result:{kind:"history",entries:[]}}}));await stale;client.dispose();
});

it("distinguishes Undo recovery bytes and restores opener focus on Cancel",async()=>{
 const run=vi.fn().mockResolvedValue({...preview,action:"undo"});render(<RestoreFile run={run} action={action} onChanged={vi.fn()}/>);
 fireEvent.click(screen.getByRole("button",{name:"Restore file…"}));await screen.findByRole("dialog");expect(screen.getByText(/pre-restore recovery copy/)).toBeInTheDocument();expect(screen.getByText(/existing recovery record/)).toBeInTheDocument();fireEvent.click(screen.getByRole("button",{name:"Cancel"}));expect(screen.getByRole("button",{name:"Restore file…"})).toHaveFocus();
});
