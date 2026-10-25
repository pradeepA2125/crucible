import {act,fireEvent,render,screen} from "@testing-library/react";import {afterEach,expect,it,vi} from "vitest";
import {FindBar,renderedFindScope} from "../desktop/FindBar";import {SourcePreview} from "../desktop/SourcePreview";import {vscode} from "../vscodeApi";
vi.mock("../vscodeApi",()=>({vscode:{postMessage:vi.fn()}}));afterEach(()=>{vi.useRealTimers();vi.clearAllMocks();});
it("uses native Find, ignores superseded replies, and clears on source page changes without Stop",()=>{
 vi.useFakeTimers();const close=vi.fn();const {rerender}=render(<FindBar open target="a" close={close}/>);
 expect(screen.getByRole("textbox",{name:"Find text"})).toHaveFocus();fireEvent.change(screen.getByRole("textbox"),{target:{value:"needle"}});act(()=>vi.advanceTimersByTime(150));
 const sent=vi.mocked(vscode.postMessage).mock.calls.map(([m])=>m as any).find(m=>m.type==="desktop/find");expect(sent.query).toBe("needle");
 act(()=>window.dispatchEvent(new MessageEvent("message",{data:{type:"desktop/findResult",requestId:"old",matches:100}})));expect(screen.getByRole("status")).toHaveTextContent("Finding");
 act(()=>window.dispatchEvent(new MessageEvent("message",{data:{type:"desktop/findResult",requestId:sent.requestId,matches:2,ordinal:1}})));expect(screen.getByRole("status")).toHaveTextContent("1 of 2");
 expect(screen.getByText(/rendered page.*2,000/)).toBeInTheDocument();rerender(<><FindBar open target="a" close={close}/><SourcePreview path="a.ts" text={Array.from({length:2001},(_,i)=>String(i)).join("\n")} wrap/></>);
 fireEvent.change(screen.getByRole("textbox"),{target:{value:"again"}});fireEvent.click(screen.getByRole("button",{name:"Next lines"}));expect(screen.getByRole("textbox")).toHaveValue("");
 fireEvent.keyDown(window,{key:"Escape"});expect(close).toHaveBeenCalledOnce();expect(vi.mocked(vscode.postMessage).mock.calls.some(([m])=>(m as any).type==="stopTurn")).toBe(false);
});

it("excludes Find input/chrome/composer from native matches and restores prior inert state",()=>{
 const {container}=render(<><header>chrome</header><div data-testid="transcript-column">transcript</div><aside id="workspace-content">preview</aside><input aria-label="composer fixture"/><div ref={node=>node?.setAttribute("inert","")}>already inert</div></>);
 const restore=renderedFindScope(document);expect(container.querySelector("header")).toHaveAttribute("inert");expect(screen.getByLabelText("composer fixture")).toHaveAttribute("inert");expect(screen.getByText("transcript")).not.toHaveAttribute("inert");restore();expect(container.querySelector("header")).not.toHaveAttribute("inert");expect(screen.getByText("already inert")).toHaveAttribute("inert");
});
