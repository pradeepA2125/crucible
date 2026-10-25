import{act,fireEvent,render,screen}from"@testing-library/react";import{afterEach,expect,it,vi}from"vitest";import{CommandPalette}from"../desktop/CommandPalette";import type{WorkspacePort}from"../desktop/workspaceClient";
afterEach(()=>vi.useRealTimers());
it("opens a real action with keyboard and retains disabled navigation protection",()=>{
 const run=vi.fn(),close=vi.fn();render(<CommandPalette items={[{id:"a",label:"Conversation A",disabled:true,run},{id:"settings",label:"Settings",run}]} close={close} openFile={()=>{}}/>);
 const input=screen.getByRole("combobox");fireEvent.keyDown(input,{key:"Enter"});expect(run).not.toHaveBeenCalled();fireEvent.keyDown(input,{key:"ArrowDown"});fireEvent.keyDown(input,{key:"Enter"});expect(run).toHaveBeenCalledOnce();expect(close).toHaveBeenCalledOnce();
});
it("opens matching Files paths, ignores superseded results and cancels on close",async()=>{
 vi.useFakeTimers();let old!:(v:any)=>void;const search=vi.fn().mockImplementationOnce(()=>new Promise(r=>old=r)).mockResolvedValue({entries:[{path:"src/new.ts",kind:"file"}],partial:true,warnings:[]});
 const files={search,cancelSearch:vi.fn()} as unknown as WorkspacePort,open=vi.fn();const view=render(<CommandPalette items={[]} files={files} openFile={open} close={()=>{}}/>);const input=screen.getByRole("combobox");
 fireEvent.change(input,{target:{value:"old"}});await act(async()=>{await vi.advanceTimersByTimeAsync(181);});fireEvent.change(input,{target:{value:"new"}});await act(async()=>{await vi.advanceTimersByTimeAsync(181);});
 await act(async()=>old({entries:[{path:"src/old.ts",kind:"file"}],partial:false,warnings:[]}));expect(screen.queryByText("src/old.ts")).toBeNull();expect(screen.getByRole("status")).toHaveTextContent("partial");fireEvent.click(screen.getByRole("option",{name:/src\/new.ts/}));expect(open).toHaveBeenCalledWith("src/new.ts");view.unmount();expect(files.cancelSearch).toHaveBeenCalled();
});
