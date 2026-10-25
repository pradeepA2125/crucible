import{act,fireEvent,render,screen}from"@testing-library/react";import{expect,it,vi}from"vitest";
import{Projects}from"../desktop/Projects";import{vscode}from"../vscodeApi";vi.mock("../vscodeApi",()=>({vscode:{postMessage:vi.fn()}}));
const post=(data:unknown)=>act(()=>window.dispatchEvent(new MessageEvent("message",{data})));
it("lists host recents with missing/unknown and exact project attention; selects only the named project",()=>{
 render(<Projects/>);post({type:"desktop/projects",selected:"a",projects:[{id:"a",name:"Alpha",root:"/alpha",missing:false,state:"idle"},{id:"b",name:"Beta",root:"/beta",missing:false,state:"busy",attention:[{threadId:"t",title:"Review edit",gates:2}]},{id:"c",name:"Gone",root:"/gone",missing:true}]});
 expect(screen.getByText("Working / review")).toBeInTheDocument();fireEvent.click(screen.getByRole("button",{name:/Review edit/}));expect(vscode.postMessage).toHaveBeenCalledWith({type:"desktop/selectProject",projectId:"b",threadId:"t"});
 expect(screen.getByRole("button",{name:/Gone Missing/})).toBeDisabled();fireEvent.click(screen.getByRole("button",{name:"Locate…"}));expect(vscode.postMessage).toHaveBeenCalledWith({type:"desktop/locateProject",projectId:"c"});
 post({type:"desktop/projectsError",message:"Still busy — kept"});expect(screen.getByRole("alert")).toHaveTextContent("Still busy");
});
it("pending navigation disables project selection",()=>{render(<Projects locked/>);post({type:"desktop/projects",selected:"a",projects:[{id:"b",name:"Beta",root:"/beta",missing:false,state:"unknown"}]});expect(screen.getByRole("button",{name:/Beta Status unknown/})).toBeDisabled();});
