import {act,cleanup,fireEvent,render,screen} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {DesktopWelcome} from "../desktop/DesktopWelcome";
import {vscode} from "../vscodeApi";
vi.mock("../vscodeApi",()=>({vscode:{postMessage:vi.fn()}}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const post=(data:unknown)=>act(()=>window.dispatchEvent(new MessageEvent("message",{data})));
it("projectless welcome uses existing project chooser without starting a conversation or acquiring authority",()=>{
 render(<DesktopWelcome/>);expect(vscode.postMessage).toHaveBeenCalledWith({type:"desktop/projectsRead"});expect(screen.queryByRole("textbox",{name:"Chat input"})).toBeNull();fireEvent.click(screen.getByRole("button",{name:"Open project…"}));expect(vscode.postMessage).toHaveBeenLastCalledWith({type:"desktop/openProject"});expect(vscode.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({type:"sendMessage"}));
});
it("selects only the host-provided recent project and preserves missing/error behavior",()=>{
 render(<DesktopWelcome/>);post({type:"desktop/projects",selected:null,projects:[{id:"a",root:"/disposable/Alpha",name:"Alpha",missing:false,state:"idle"},{id:"b",root:"/disposable/Beta",name:"Beta",missing:true}]});fireEvent.click(screen.getByRole("button",{name:/Alpha Ready/}));expect(vscode.postMessage).toHaveBeenLastCalledWith({type:"desktop/selectProject",projectId:"a"});expect(screen.getByRole("button",{name:/Beta Missing/})).toBeDisabled();post({type:"desktop/projectsError",message:"Project is still controlled elsewhere."});expect(screen.getByRole("alert")).toHaveTextContent("controlled elsewhere");
});
it("removal confirmation uses safe initial focus and Escape without deleting drafts",()=>{
 render(<DesktopWelcome/>);post({type:"desktop/projects",selected:null,projects:[{id:"a",root:"/disposable/Alpha",name:"Alpha",missing:false}]});fireEvent.click(screen.getByLabelText("Actions for Alpha"));const trigger=screen.getByRole("button",{name:"Remove from Recents…"});fireEvent.click(trigger);expect(screen.getByRole("dialog",{name:"Remove project"})).toBeInTheDocument();expect(screen.getByRole("button",{name:"Cancel"})).toHaveFocus();fireEvent.keyDown(document,{key:"Escape"});expect(screen.queryByRole("dialog")).toBeNull();expect(trigger).toHaveFocus();expect(vscode.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({type:"desktop/removeProject"}));
});
