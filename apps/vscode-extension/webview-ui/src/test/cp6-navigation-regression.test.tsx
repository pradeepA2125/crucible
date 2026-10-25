import{act,render,screen,fireEvent}from"@testing-library/react";import{it,expect,vi}from"vitest";import App from"../App";import{vscode}from"../vscodeApi";
vi.mock("../vscodeApi",()=>({vscode:{postMessage:vi.fn()}}));const host=(data:unknown)=>act(()=>{window.dispatchEvent(new MessageEvent("message",{data}))});
it("independent: safe busy VS Code Back reaches conversation history without Stop",async()=>{
 render(<App/>);host({type:"renderThreadList",threads:[{threadId:"a",title:"Conversation A",createdAt:"x"},{threadId:"b",title:"Conversation B",createdAt:"x"}],activeThreadId:"a"});host({type:"clearThread",threadId:"a"});fireEvent.click(screen.getByText("Conversation A"));
 host({type:"historyState",history:{threadId:"a",phase:"current",cached:true,navigationSafe:true,workBlocked:"A working"}});host({type:"liveStatus",status:null,turnActive:true,turnKind:"user"});host({type:"setInputEnabled",enabled:false});
 const back=screen.getByRole("button",{name:"Back to history"});expect(back).toBeEnabled();fireEvent.click(back);await act(async()=>{});
 expect(screen.queryByRole("button",{name:"New Chat"})).not.toBeNull();expect(vscode.postMessage).not.toHaveBeenCalledWith({type:"stopTurn"});
});