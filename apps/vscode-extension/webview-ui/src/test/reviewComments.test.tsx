import { useState } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DiffLines, parseUnifiedDiff } from "../components/shared/DiffPanes";
import { InputArea } from "../components/InputArea";
import { ReviewDraft, reviewCommentText, REVIEW_APPEND_BYTES, type RegisterReviewDraft, type ReviewDraftSink } from "../drafts/reviewDraft";
import type { DraftPort, DraftSnapshot } from "../drafts/types";
import { DraftStore } from "../../../../desktop/src/draft-store";
import DesktopApp from "../desktop/DesktopApp";
import type { ChatMsg } from "../types";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const patch="@@ -3,2 +8,2 @@\n context\n-old\n+new", source={identity:"record1",label:"Conversation edits · turn 2 · edit 3"};
const data=(text:string)=>({text,imageAttachments:[],mentionedPaths:[]});
function fixture(){
 const store=new DraftStore(),listeners=new Set<(s:DraftSnapshot)=>void>();let sink:ReviewDraftSink|undefined,flush=async()=>{};
 const register:RegisterReviewDraft=value=>{sink=value;return()=>{if(sink===value)sink=undefined;};};
 const port:DraftPort={read:vi.fn(async id=>store.read(id)),save:vi.fn(async(id,r,d)=>store.save(id,r,d)),begin:vi.fn(async(id,r,d,key)=>store.begin(id,r,d,key)),choose:vi.fn(async(id,r,v,d,c)=>store.choose(id,r,v,d,c)),onChange:fn=>{listeners.add(fn);return()=>{listeners.delete(fn);};},onFlush:fn=>{flush=fn;return()=>{};}};
 return {store,port,register,sink:()=>sink,flush:()=>flush(),changed:(s:DraftSnapshot)=>listeners.forEach(fn=>fn(s))};
}
function Composer({f,threadId="a"}:{f:ReturnType<typeof fixture>;threadId?:string}){const[text,setText]=useState("");return <InputArea threadId={threadId} draftStore={f.port} draft={text} onDraftChange={setText} registerReviewDraft={f.register} availability={{disabled:false,showStop:false,taskStop:false,placeholder:"Message"}}/>;}
it("tracks actual old/new positions, zero ranges and fresh hunks without inventing lines after malformed input",()=>{
 const lines=parseUnifiedDiff(patch);expect(lines[1]).toMatchObject({oldNumber:3,newNumber:8});expect(lines[2]).toMatchObject({oldNumber:4});expect(lines[2].newNumber).toBeUndefined();expect(lines[3]).toMatchObject({newNumber:9});
 expect(parseUnifiedDiff("@@ -0,0 +1,1 @@\n+created")[1]).toMatchObject({newNumber:1});
 const malformed=parseUnifiedDiff("@@ -1,1 +1,1 @@\nunprefixed\n+unknown\n@@ -7,1 +0,0 @@\n-deleted\n\\ No newline at end of file");
 expect(malformed[1].newNumber).toBeUndefined();expect(malformed[2].newNumber).toBeUndefined();expect(malformed[4]).toMatchObject({oldNumber:7});expect(malformed[5].oldNumber).toBeUndefined();
 expect(parseUnifiedDiff("@@ -9007199254740992,1 +1,1 @@\n+unsafe")[1].newNumber).toBeUndefined();
 const overflow=parseUnifiedDiff("@@ -1,1 +1,1 @@\n+ok\n+outside\n-del");expect(overflow[2].newNumber).toBeUndefined();expect(overflow[3].oldNumber).toBeUndefined();
});
it("fences arbitrary captured code safely, quotes identity text and bounds excerpts without starting a slash command",()=>{
 const text=reviewCommentText('a\n<script>.ts',source,{text:'```\n/command\n'+"x".repeat(1100),oldNumber:4},"Please review");
 expect(text.startsWith("Review comment (recorded excerpt)")).toBe(true);expect(text).toContain('File: "a\\n<script>.ts" · old line 4');expect(text).toContain("````text\n```\n/command");expect(text).toContain("[Excerpt shortened");expect(text).toContain("Note: Please review");
});
it("keeps compact and unregistered review paths free of comment controls",()=>{
 const sink={threadId:"a",ready:true,append:vi.fn()},ui=render(<ReviewDraft.Provider value={sink}><DiffLines diff={patch} source={source}/></ReviewDraft.Provider>);
 expect(screen.queryByRole("button",{name:/Comment on/})).toBeNull();ui.rerender(<DiffLines diff={patch} path="a.ts" presentation="review" source={source}/>);expect(screen.queryByRole("button",{name:/Comment on/})).toBeNull();
});
it("appends the selected old-side excerpt once, stays editable and refuses stale source/thread forms",()=>{
 const append=vi.fn(),sink={threadId:"a",ready:true,append},draw=(identity="record1",threadId="a")=><ReviewDraft.Provider value={{...sink,threadId}}><DiffLines diff={patch} path="a.ts" presentation="review" source={{...source,identity}}/></ReviewDraft.Provider>;
 const ui=render(draw());fireEvent.click(screen.getByRole("button",{name:"Comment on a.ts, old line 4"}));fireEvent.change(screen.getByRole("textbox",{name:"Review note"}),{target:{value:"Keep the old behavior"}});
 const form=screen.getByRole("form");act(()=>{fireEvent.submit(form);fireEvent.submit(form);});expect(append).toHaveBeenCalledTimes(1);expect(append.mock.calls[0][0]).toContain("old line 4\n```text\nold\n```");
 fireEvent.click(screen.getByRole("button",{name:"Comment on a.ts, new line 9"}));fireEvent.change(screen.getByRole("textbox",{name:"Review note"}),{target:{value:"A note"}});ui.rerender(draw("record2"));expect(screen.getByRole("button",{name:"Add to draft"})).toBeDisabled();expect(screen.getByText(/comparison or conversation changed/)).toBeInTheDocument();
 ui.rerender(draw("record1","b"));expect(screen.getByRole("button",{name:"Add to draft"})).toBeDisabled();expect(append).toHaveBeenCalledTimes(1);
});
it("does not expose hunk/no-newline/unsafe lines, and retains exact positions when folded context expands",()=>{
 const append=vi.fn();render(<ReviewDraft.Provider value={{threadId:"a",ready:true,append}}><DiffLines path="a.ts" presentation="review" source={source} diff={'@@ -1,12 +1,12 @@\n'+Array.from({length:12},(_,i)=>` context${i+1}`).join('\n')+'\n\\ No newline at end of file'}/></ReviewDraft.Provider>);
 expect(screen.queryByRole("button",{name:"Comment on a.ts, old line 8 / new line 8"})).toBeNull();
 const third=screen.getByRole("button",{name:"Comment on a.ts, old line 3 / new line 3"});act(()=>third.focus());fireEvent.keyDown(third,{key:"ArrowDown"});expect(screen.getByRole("button",{name:"Comment on a.ts, old line 10 / new line 10"})).toHaveFocus();
 fireEvent.click(screen.getByRole("button",{name:/unchanged lines · expand/}));fireEvent.click(screen.getByRole("button",{name:"Comment on a.ts, old line 8 / new line 8"}));fireEvent.change(screen.getByRole("textbox",{name:"Review note"}),{target:{value:"This context"}});fireEvent.click(screen.getByRole("button",{name:"Add to draft"}));expect(append.mock.calls[0][0]).toContain("old line 8 / new line 8\n```text\ncontext8");
});
it("waits for late hydration, keeps text/images/mentions and two rapid appends, preserves focus and refuses oversize atomically",async()=>{
 const f=fixture(),image={name:"diagram.png",mediaType:"image/png",dataUrl:"data:image/png;base64,AA==",bytes:1};let release!:(s:DraftSnapshot)=>void;
 f.store.save("a",1,{text:"existing @a.ts",imageAttachments:[image],mentionedPaths:["a.ts"]});vi.mocked(f.port.read).mockImplementationOnce(()=>new Promise(resolve=>{release=resolve;}));
 render(<><input aria-label="Other"/><Composer f={f}/></>);expect(f.sink()?.ready).toBe(false);expect(f.sink()?.append("too early")).toContain("load");await act(async()=>release(f.store.read("a")));await waitFor(()=>expect(f.sink()?.ready).toBe(true));expect(screen.getByAltText("diagram.png")).toBeInTheDocument();
 const other=screen.getByRole("textbox",{name:"Other"});other.focus();act(()=>{expect(f.sink()!.append("block one")).toBeUndefined();expect(f.sink()!.append("block two")).toBeUndefined();});expect(other).toHaveFocus();expect(screen.getByRole("textbox",{name:"Chat input"})).toHaveValue("existing @a.ts\n\nblock one\n\nblock two");await act(()=>f.flush());expect(f.store.read("a").draft).toMatchObject({imageAttachments:[image],mentionedPaths:["a.ts"]});
 act(()=>expect(f.sink()!.append("日".repeat(REVIEW_APPEND_BYTES))).toContain("size limit"));expect(screen.getByRole("textbox",{name:"Chat input"})).toHaveValue("existing @a.ts\n\nblock one\n\nblock two");
});
it("blocks failed hydration, empty thread, pending prompt expansion and returned drafts",async()=>{
 const f=fixture();vi.mocked(f.port.read).mockRejectedValueOnce(Error("Controlled draft failure"));const ui=render(<Composer f={f}/>);await screen.findByText("Controlled draft failure");expect(f.sink()?.ready).toBe(false);ui.unmount();
 const g=fixture(),view=render(<Composer f={g} threadId=""/>);await act(async()=>{});expect(g.sink()?.ready).toBe(false);view.unmount();
 const h=fixture();h.store.save("a",1,data("/example args"));render(<Composer f={h}/>);await waitFor(()=>expect(h.sink()?.ready).toBe(true));fireEvent.click(screen.getByRole("button",{name:"Send"}));expect(h.sink()?.ready).toBe(false);expect(h.sink()?.append("comment")).toContain("prompt expansion");
 const post=(await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>,request=post.mock.calls.map(([m])=>m).filter(m=>m.type==="expandPrompt").at(-1);
 host({type:"promptExpanded",requestId:request.requestId,found:true,text:"expanded"});await waitFor(()=>expect(h.sink()?.ready).toBe(true));
 h.store.begin("a",10,data("earlier pending"),"returned");h.store.save("a",11,data("expanded"));act(()=>h.changed(h.store.reject("returned")!));await waitFor(()=>expect(h.sink()?.ready).toBe(false));expect(h.sink()?.append("comment")).toContain("other unsent draft");
});
it("preserves a review suffix appended during retained send admission and rejects an unmounted composer",async()=>{
 const f=fixture();f.store.save("a",1,data("original"));let release!:()=>void;vi.mocked(f.port.begin).mockImplementation((id,r,d,key)=>new Promise(resolve=>{release=()=>resolve(f.store.begin(id,r,d,key));}));const ui=render(<Composer f={f}/>);await waitFor(()=>expect(f.sink()?.ready).toBe(true));fireEvent.click(screen.getByRole("button",{name:"Send"}));await waitFor(()=>expect(f.port.begin).toHaveBeenCalledOnce());act(()=>expect(f.sink()!.append("review suffix")).toBeUndefined());await act(async()=>release());expect(screen.getByRole("textbox",{name:"Chat input"})).toHaveValue("\n\nreview suffix");const old=f.sink()!;ui.unmount();expect(old.append("stale")).toContain("not ready");expect(f.sink()).toBeUndefined();
});
const host=(data:unknown)=>act(()=>window.dispatchEvent(new MessageEvent("message",{data})));
const ready=(id="a")=>{host({type:"renderThreadList",activeThreadId:id,threads:["a","b"].map(threadId=>({threadId,title:threadId,createdAt:"now"}))});host({type:"clearThread",threadId:id});host({type:"liveStatus",threadId:id,turnActive:false,agentsRunning:0});};
it("uses the actual desktop main composer, keeps editable comments through workspace toggles and never sends",async()=>{
 const post=(await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>;post.mockClear();window.innerWidth=1440;render(<DesktopApp/>);ready();
 const card:ChatMsg={role:"agent",type:"diff_card",content:"",timestamp:"now",metadata:{resolved:"applied",diff_entries:[{path:"a.ts",additions:1,deletions:1,unified_diff:patch}]}};host({type:"appendMessage",message:card});const input=screen.getByRole("textbox",{name:"Chat input"});fireEvent.change(input,{target:{value:"existing draft"}});fireEvent.click(screen.getByRole("button",{name:"Show diff for a.ts in Changes"}));const review=screen.getByRole("region",{name:"Changes"});fireEvent.click(within(review).getByRole("button",{name:"Comment on a.ts, new line 9"}));fireEvent.change(screen.getByRole("textbox",{name:"Review note"}),{target:{value:"Please revise this line"}});fireEvent.click(screen.getByRole("button",{name:"Add to draft"}));expect((input as HTMLTextAreaElement).value).toContain("existing draft\n\nReview comment");expect((input as HTMLTextAreaElement).value).toContain("new line 9");fireEvent.click(screen.getByRole("button",{name:"Hide workspace"}));fireEvent.click(screen.getByRole("button",{name:"Open workspace"}));expect((input as HTMLTextAreaElement).value).toContain("Please revise");expect(post.mock.calls.some(([m])=>m.type==="sendMessage")).toBe(false);
 fireEvent.click(screen.getByRole("button",{name:"Comment on a.ts, old line 4"}));fireEvent.change(screen.getByRole("textbox",{name:"Review note"}),{target:{value:"Do not move this to b"}});ready("b");const add=screen.queryByRole("button",{name:"Add to draft"});if(add)expect(add).toBeDisabled();expect(screen.getByRole("textbox",{name:"Chat input"})).toHaveValue("");
});

it.each(["git","baseline"] as const)("quotes actual %s scope provenance through the shared desktop renderer",async(kind)=>{
 const generation="12345678-1234-4234-8234-123456789abc",checkpoint={seq:0,anchorMessageId:"anchor",createdAt:"2026-10-22T12:00:00Z"};
 const diff={kind:"text" as const,unified:patch,additions:1,deletions:1,truncated:false,fingerprint:"f".repeat(64)};
 const git:import("../desktop/changesClient").ChangesPort={scope:async()=>({state:"ready",generation,head:"a".repeat(40),changes:[{path:"a.ts",status:"M"}],uncompared:[],partial:false,rawBytes:true,warnings:[]}),diff:async()=>diff,project:()=>"p",onProjectChange:()=>()=>{},dispose:()=>{}};
 const baseline:import("../desktop/baselineClient").BaselinePort={list:async()=>({state:"ready",checkpoints:[checkpoint],omitted:0}),scope:async()=>({state:"ready",generation,checkpoint,changes:[{path:"a.ts",status:"M",fromSeq:0,supplying:checkpoint}],unchanged:0,createdRemoved:0,uncompared:[],omitted:0,partial:false}),diff:async()=>diff,context:()=>({viewId:"v",projectId:"p",threadId:"a"}),onContextChange:()=>()=>{},dispose:()=>{}};
 window.innerWidth=1440;render(<DesktopApp changesStore={kind==="git"?git:undefined} baselineStore={kind==="baseline"?baseline:undefined}/>);ready();
 fireEvent.click(screen.getByRole("button",{name:"Open workspace"}));fireEvent.click(screen.getByRole("button",{name:"New workspace tab"}));fireEvent.click(screen.getByRole("button",{name:"Changes"}));fireEvent.change(screen.getByRole("combobox",{name:"Changes scope"}),{target:{value:kind}});
 if(kind==="baseline"){await screen.findByRole("option",{name:/22/});fireEvent.change(screen.getByRole("combobox",{name:"Retained checkpoint"}),{target:{value:"0"}});}
 fireEvent.click(await screen.findByRole("button",{name:"Comment on a.ts, new line 9"}));fireEvent.change(screen.getByRole("textbox",{name:"Review note"}),{target:{value:"Check this captured line"}});fireEvent.click(screen.getByRole("button",{name:"Add to draft"}));
 const text=(screen.getByRole("textbox",{name:"Chat input"}) as HTMLTextAreaElement).value;expect(text).toContain('File: "a.ts" · new line 9');expect(text).toContain("```text\nnew\n```");
 if(kind==="git")expect(text).toContain("Uncommitted vs HEAD "+"a".repeat(40));else{expect(text).toContain("Agent edits since");expect(text).toContain("retained content supplied by");expect(text).not.toContain("HEAD");}
});

it("uses one gutter tab stop per diff with arrow/Home/End navigation", () => {
 render(<ReviewDraft.Provider value={{ threadId:"a", ready:true, append:vi.fn() }}><DiffLines diff={patch} path="a.ts" presentation="review" source={source}/></ReviewDraft.Provider>);
 const buttons=screen.getAllByRole("button",{name:/Comment on/});expect(buttons.filter(button=>button.tabIndex===0)).toHaveLength(1);
 act(()=>buttons[0].focus());fireEvent.keyDown(buttons[0],{key:"ArrowDown"});expect(buttons[1]).toHaveFocus();expect(buttons[0]).toHaveAttribute("tabindex","-1");
 fireEvent.keyDown(buttons[1],{key:"End"});expect(buttons.at(-1)).toHaveFocus();fireEvent.keyDown(buttons.at(-1)!,{key:"Home"});expect(buttons[0]).toHaveFocus();
});
it("keeps a complete astral character at the excerpt boundary", () => {
 const text=reviewCommentText("a.ts",source,{text:"x".repeat(999)+"😀tail",newNumber:1},"note");expect(text).toContain("😀\n```");expect(text).not.toContain("😀tail");expect(text).toContain("Excerpt shortened");
});
