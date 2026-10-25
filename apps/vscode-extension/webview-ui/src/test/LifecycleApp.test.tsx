import{act,cleanup,fireEvent,render,screen}from"@testing-library/react";import{afterEach,expect,it,vi}from"vitest";import LifecycleApp from"../desktop/LifecycleApp";
afterEach(cleanup);const post=(preview:unknown)=>act(()=>window.dispatchEvent(new MessageEvent("message",{data:{type:"desktop/quitPreview",preview}})));
it("shows real resource names, unknown/observer state and explicit safe actions using shared cards",()=>{
 const send=vi.fn();render(<LifecycleApp send={send}/>);post({id:"snapshot",phase:"preview",projects:[{id:"p",name:"Alpha",state:"unknown",owner:false,shells:[{id:"s",label:"zsh"}],threads:[{thread_id:"t",title:"Design",turn_active:true,gates:2,agents:[{id:"a",label:"Reviewer"}],teams:[{id:"team",label:"Builders"}],sessions:[{id:"cmd",label:"test"}]}]}]});
 expect(screen.getByRole("dialog")).toBeInTheDocument();expect(screen.getByRole("button",{name:"Cancel"})).toHaveFocus();expect(screen.getByText("Alpha").closest(".rounded-card")).toBeInTheDocument();expect(screen.getByText(/Activity could not/)).toBeInTheDocument();expect(screen.getByText("Agent · Reviewer")).toBeInTheDocument();expect(screen.getByText("Team · Builders")).toBeInTheDocument();expect(screen.getByText("Command · test")).toBeInTheDocument();
 fireEvent.keyDown(document,{key:"Escape"});expect(send).toHaveBeenCalledWith({type:"desktop/lifecycleChoice",snapshotId:"snapshot",choice:"cancel"});
});
it("never silently discards a failed draft save or offers background after partial cleanup",()=>{
 const send=vi.fn();render(<LifecycleApp send={send}/>);post({id:"draft",projects:[],phase:"draft-error",error:"Save failed"});expect(screen.getByRole("alert")).toHaveTextContent("Save failed");
 fireEvent.click(screen.getByRole("button",{name:"Discard unsaved edits and quit"}));expect(send).toHaveBeenCalledWith({type:"desktop/lifecycleChoice",snapshotId:"draft",choice:"discard"});
 post({id:"cleanup",projects:[],phase:"cleanup-error",error:"Retry"});expect(screen.queryByRole("button",{name:"Cancel"})).toBeNull();expect(screen.queryByRole("button",{name:"Keep running"})).toBeNull();expect(screen.getByRole("button",{name:"Retry cleanup and quit"})).toBeEnabled();
});
