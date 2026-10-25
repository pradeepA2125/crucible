import {render,screen,fireEvent,waitFor} from "@testing-library/react";
import {it,expect,vi}from"vitest";
import {TerminalTool}from"../desktop/TerminalTool";
vi.mock("@xterm/xterm",()=>({Terminal:class {textarea=document.createElement("textarea");parser={registerOscHandler:()=>({dispose(){}})};options:any={};cols=80;rows=24;loadAddon(){}open(){}onData(){return{dispose(){}};}write(_d:unknown,cb:()=>void){cb();}writeln(){}reset(){}dispose(){}}}));
vi.mock("@xterm/addon-fit",()=>({FitAddon:class{fit(){}}}));
const id="8226da44-619f-4ea3-9ede-35ab9979859b";
const exited={id,projectId:"p",provenance:"user" as const,initialCwd:"/test",shell:"/bin/zsh",status:"exited" as const,exitCode:0,hostAlive:false,start:0,end:0,cols:80,rows:24};
it("opening the terminal only reads; agent output is read only and exited shells have no interrupt/end",async()=>{
 vi.stubGlobal("ResizeObserver",class{observe(){}disconnect(){}});
 const request=vi.fn(async(r:any)=>r.operation==="list"?{sessions:[exited]}:r.operation==="agentList"?{agents:[]}:r.operation==="read"?{session:exited,data:"",next:0,reset:false}:{});
 const view=render(<TerminalTool threadId="t" port={{request,dispose:vi.fn()}}/>);
 await screen.findByText(/No retained agent/);expect(request.mock.calls.every(([r])=>["list","agentList"].includes(r.operation))).toBe(true);
 expect(screen.queryByText("Stop")).toBeNull();fireEvent.click(screen.getByRole("button",{name:"Your shell"}));await screen.findByText(/Initial cwd: \/test/);
 expect(screen.queryByRole("button",{name:"Interrupt"})).toBeNull();expect(screen.queryByRole("button",{name:"End session"})).toBeNull();expect(screen.getByRole("button",{name:"Launch shell…"})).toBeTruthy();
 view.unmount();expect(request.mock.calls.some(([r])=>r.operation==="end")).toBe(false);vi.unstubAllGlobals();
});
it("only the explicit launch button requests a new shell; no automatic agent command",async()=>{
 const request=vi.fn(async(r:any)=>r.operation==="list"?{sessions:[]}:r.operation==="agentList"?{agents:[]}:{session:exited});
 render(<TerminalTool threadId="t" port={{request,dispose:vi.fn()}}/>);await screen.findByText(/No retained agent/);fireEvent.click(screen.getByRole("button",{name:"Your shell"}));fireEvent.click(screen.getByRole("button",{name:"Launch shell…"}));await waitFor(()=>expect(request).toHaveBeenCalledWith({operation:"start",cols:100,rows:30}));expect(request.mock.calls.some(([r])=>r.operation==="input")).toBe(false);
});
