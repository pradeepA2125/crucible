import{act,fireEvent,render,screen}from"@testing-library/react";import{afterEach,expect,it,vi}from"vitest";import{AppearanceSection}from"../settings/sections/AppearanceSection";import{installThemeSync}from"../theme";import DesktopApp from"../desktop/DesktopApp";vi.mock("../vscodeApi",()=>({vscode:{postMessage:vi.fn()}}));
afterEach(()=>{delete document.documentElement.dataset.host;});
it("uses desktop Follow system labels with the existing appearance contract",()=>{
 const stop=installThemeSync();act(()=>window.dispatchEvent(new MessageEvent("message",{data:{type:"themeChanged",host:"desktop",appearance:"dark",accent:"blue"}})));const send=vi.fn();render(<AppearanceSection appearance="vscode" accent="blue" busy={false} send={send}/>);const system=screen.getByRole("radio",{name:/Follow system/});expect(system).toBeChecked();fireEvent.click(screen.getByRole("radio",{name:/Ember/}));expect(send).toHaveBeenCalledWith({type:"settings\/setAppearance",appearance:"vscode",accent:"ember"});stop();
});
it("opens the palette with CmdK and toggles summary independently of workspace",()=>{
 render(<DesktopApp/>);fireEvent.keyDown(window,{key:"k",metaKey:true});expect(screen.getByRole("dialog",{name:/Search conversations/})).toBeInTheDocument();fireEvent.keyDown(document,{key:"Escape"});expect(screen.queryByRole("dialog")).toBeNull();fireEvent.click(screen.getByRole("button",{name:"Show pinned summary"}));expect(screen.getByRole("complementary",{name:"Pinned conversation summary"})).toBeInTheDocument();expect(screen.getByRole("button",{name:"Open workspace"})).toHaveAttribute("aria-expanded","false");fireEvent.click(screen.getByRole("button",{name:"Hide pinned summary"}));expect(screen.queryByRole("complementary",{name:"Pinned conversation summary"})).toBeNull();
});
it("routes native shortcuts through visible controls and keeps modal priority",async()=>{
 const {vscode}=await import("../vscodeApi");vi.mocked(vscode.postMessage).mockClear();render(<DesktopApp/>);
 const shortcut=(action:string)=>act(()=>window.dispatchEvent(new MessageEvent("message",{data:{type:"desktop/shortcut",action}})));
 shortcut("sidebar");expect(screen.getByRole("button",{name:"Show conversations"})).toBeInTheDocument();shortcut("sidebar");
 shortcut("settings");expect(vscode.postMessage).toHaveBeenCalledWith({type:"openSettings"});
 expect(screen.queryByRole("button",{name:/Runtime status/})).toBeNull();
 fireEvent.click(screen.getByRole("button",{name:"Settings"}));expect(vscode.postMessage).toHaveBeenCalledWith({type:"openSettings"});
 shortcut("palette");vi.mocked(vscode.postMessage).mockClear();shortcut("open");expect(vscode.postMessage).not.toHaveBeenCalled();
 fireEvent.keyDown(document,{key:"Escape"});shortcut("find");expect(screen.getByRole("search",{name:"Find in visible view"})).toBeInTheDocument();fireEvent.keyDown(window,{key:"Escape"});expect(screen.queryByRole("search")).toBeNull();
 expect(vi.mocked(vscode.postMessage).mock.calls.some(([m])=>(m as any).type==="stopTurn")).toBe(false);
});

it("preserves Mac CtrlK readline while CmdK still opens the palette",()=>{
 const prior=Object.getOwnPropertyDescriptor(navigator,"platform");Object.defineProperty(navigator,"platform",{configurable:true,value:"MacIntel"});
 try{render(<DesktopApp/>);const event=new KeyboardEvent("keydown",{key:"k",ctrlKey:true,cancelable:true});act(()=>window.dispatchEvent(event));expect(event.defaultPrevented).toBe(false);expect(screen.queryByRole("dialog")).toBeNull();fireEvent.keyDown(window,{key:"k",metaKey:true});expect(screen.getByRole("dialog")).toBeInTheDocument();}finally{if(prior)Object.defineProperty(navigator,"platform",prior);else delete (navigator as any).platform;}
});
