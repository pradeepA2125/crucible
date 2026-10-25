import{act,fireEvent,render,screen}from"@testing-library/react";import{expect,it,vi}from"vitest";
import{ProjectAccess}from"../desktop/ProjectAccess";import{vscode}from"../vscodeApi";vi.mock("../vscodeApi",()=>({vscode:{postMessage:vi.fn()}}));
const post=(data:unknown)=>act(()=>window.dispatchEvent(new MessageEvent("message",{data})));
it("offers an explicit trust action only to the current controlling project and keeps shared title separate",()=>{
 render(<ProjectAccess threadId="a" title="Backend title"/>);post({type:"desktop/access",state:"owner",workspace:"/project",processId:"p",pid:42,client:"Desktop",trusted:false,message:"Session controlled here"});fireEvent.click(screen.getByRole("button",{name:"Owner · Untrusted"}));
 expect(screen.getByRole("dialog",{name:"Project access"})).toBeInTheDocument();expect(screen.getByText(/does not select Full access/)).toBeInTheDocument();fireEvent.click(screen.getByRole("button",{name:"Trust this project session"}));expect(vscode.postMessage).toHaveBeenCalledWith({type:"desktop/trust",processId:"p",trusted:true});
 post({type:"desktop/accessError",message:"Wait for current project work"});expect(screen.getByRole("alert")).toHaveTextContent("Wait for current project work");
 fireEvent.change(screen.getByRole("textbox",{name:"Shared conversation title"}),{target:{value:"Shared title"}});fireEvent.click(screen.getByRole("button",{name:"Save shared title"}));expect(vscode.postMessage).toHaveBeenCalledWith({type:"desktop/renameShared",threadId:"a",title:"Shared title"});
});
it("observer sees owner PID/recovery and no trust or shared mutation controls",()=>{
 render(<ProjectAccess threadId="a" title="title"/>);post({type:"desktop/access",state:"observer",workspace:"/project",pid:42,client:"VS Code",message:"Read only. Close controlling client, then reopen here."});fireEvent.click(screen.getByRole("button",{name:"Observer · Read only"}));expect(screen.getByText(/VS Code.*PID 42/)).toBeInTheDocument();expect(screen.queryByText("Trust this project session")).toBeNull();expect(screen.queryByText("Save shared title")).toBeNull();
});

it("project access focuses safe Close first and returns to its opener on Escape",()=>{
 render(<ProjectAccess threadId="a" title="title"/>);post({type:"desktop/access",state:"owner",workspace:"/project",processId:"p",client:"Desktop",trusted:false,message:"Session controlled here"});const trigger=screen.getByRole("button",{name:"Owner · Untrusted"});fireEvent.click(trigger);expect(screen.getByRole("button",{name:"Close"})).toHaveFocus();fireEvent.keyDown(document,{key:"Escape"});expect(screen.queryByRole("dialog",{name:"Project access"})).toBeNull();expect(trigger).toHaveFocus();
});
