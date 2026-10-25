import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createBaselineClient, type BaselinePort } from "../desktop/baselineClient";
import { useBaselineChanges } from "../desktop/useBaselineChanges";
import { BaselineChangesTool, checkpointLabel } from "../desktop/BaselineChangesTool";
import type { ChatMsg } from "../types";
const chosen={seq:0,anchorMessageId:"anchor",createdAt:"2026-10-22T12:00:00Z"},generation="12345678-1234-4234-8234-123456789abc";
const scope={state:"ready",generation,checkpoint:chosen,changes:[{path:"a.ts",status:"M",fromSeq:0,supplying:chosen}],unchanged:0,createdRemoved:0,uncompared:[],omitted:0,partial:false};
const listing={state:"ready",checkpoints:[chosen],omitted:0},text={kind:"text",unified:"@@ -1 +1 @@\n-old\n+new",additions:1,deletions:1,truncated:false,fingerprint:"a".repeat(64)};
const clients:BaselinePort[]=[];afterEach(()=>{cleanup();clients.splice(0).forEach(port=>port.dispose());});
const event=(data:unknown)=>window.dispatchEvent(new MessageEvent("message",{data,source:window,origin:"app://app"}));
const context=(threadId="A")=>event({type:"desktop/baselineContext",viewId:"view",projectId:"project",threadId});
const setup=()=>{
 const send=vi.fn(),port=createBaselineClient(send);clients.push(port);context();
 const reply=(index:number,result:unknown,status="ok")=>{const request=send.mock.calls[index]![0];event({type:"desktop/baselineResult",viewId:"view",projectId:"project",threadId:request.threadId,requestId:request.requestId,
 operation:request.type==="desktop/baselineList"?"list":request.type==="desktop/baselineScope"?"scope":"diff",...(request.type==="desktop/baselineDiff"?{generation:request.generation,path:request.path}:{}),outcome:status==="ok"?{status,result}:{status,message:"Controlled reason"}});};
 return {send,port,reply};
};
const hookFixture=()=>{const f=setup(),hook=renderHook(()=>useBaselineChanges(f.port),{wrapper:StrictMode});
 const open=async()=>{let work!:Promise<unknown>;act(()=>{work=hook.result.current.open();});await act(async()=>{f.reply(f.send.mock.calls.length-1,listing);await work;});};
 const choose=async(value:unknown=scope)=>{let work!:Promise<void>;act(()=>{work=hook.result.current.choose(chosen);});await act(async()=>f.reply(f.send.mock.calls.length-1,value));if(f.send.mock.calls.at(-1)![0].type==="desktop/baselineDiff")await act(async()=>{f.reply(f.send.mock.calls.length-1,text);await work;});else await act(async()=>work);};
 return {...f,...hook,open,choose};};
it("loads only explicit metadata, waits for a picker choice and reuses the existing diff cache",async()=>{
 const f=hookFixture();expect(f.send).not.toHaveBeenCalled();await f.open();expect(f.send).toHaveBeenCalledTimes(1);expect(f.result.current.result).toBeUndefined();
 await f.choose();expect(f.send.mock.calls.map(([request])=>request.type)).toEqual(["desktop/baselineList","desktop/baselineScope","desktop/baselineDiff"]);expect(f.result.current.facts).toEqual(scope);expect(f.result.current.detail?.result).toEqual(text);
 act(()=>f.result.current.select("a.ts"));expect(f.send).toHaveBeenCalledTimes(3);
});
it("re-lists on Refresh, preserves the same checkpoint and checks Viewed against new bytes",async()=>{
 const f=hookFixture();await f.open();await f.choose();act(()=>f.result.current.setViewed("a.ts",true));expect(f.result.current.isViewed("a.ts")).toBe(true);
 let work!:Promise<void>;act(()=>{work=f.result.current.refresh();});expect(f.send.mock.calls[3]![0].type).toBe("desktop/baselineList");await act(async()=>f.reply(3,listing));expect(f.send.mock.calls[4]![0].type).toBe("desktop/baselineScope");
 await act(async()=>f.reply(4,{...scope,generation:"12345678-1234-4234-8234-123456789abd"}));expect(f.result.current.needsRecheck("a.ts")).toBe(true);await act(async()=>{f.reply(5,text);await work;});expect(f.result.current.isViewed("a.ts")).toBe(true);
});
it("never substitutes a reused seq or another retained checkpoint during Refresh",async()=>{
 const f=hookFixture();await f.open();await f.choose();let work!:Promise<void>;act(()=>{work=f.result.current.refresh();});
 await act(async()=>{f.reply(3,{...listing,checkpoints:[{...chosen,anchorMessageId:"reused",createdAt:"2026-10-23T00:00:00Z"}]});await work;});
 expect(f.result.current.checkpoint).toBeUndefined();expect(f.result.current.result).toBeUndefined();expect(f.result.current.error).toContain("changed or was removed");expect(f.send).toHaveBeenCalledTimes(4);
});
it("clears thread picker/facts immediately and does not read on thread rendering or a late old reply",async()=>{
 const f=hookFixture();await f.open();await f.choose();let work!:Promise<void>;act(()=>{work=f.result.current.refresh();});act(()=>context("B"));expect(f.result.current.listing).toBeUndefined();expect(f.result.current.checkpoint).toBeUndefined();expect(f.result.current.result).toBeUndefined();
 await act(async()=>{f.reply(3,listing);await work;});expect(f.result.current.listing).toBeUndefined();expect(f.result.current.error).toBe("");expect(f.send).toHaveBeenCalledTimes(4);
});
it("refuses choices outside the actual list and handles unavailable metadata distinctly from empty",async()=>{
 const f=hookFixture();await act(async()=>f.result.current.choose(chosen));expect(f.send).not.toHaveBeenCalled();expect(f.result.current.error).toContain("current list");
 let work!:Promise<unknown>;act(()=>{work=f.result.current.open();});await act(async()=>{f.reply(0,{state:"unavailable",reason:"Retained checkpoints aren't available for this conversation."});await work;});expect(f.result.current.listing).toMatchObject({state:"unavailable"});expect(f.result.current.result).toBeUndefined();
});
const messages:ChatMsg[]=[{role:"user",type:"text",id:"anchor",content:"Update the sidebar\nwith nested files",timestamp:chosen.createdAt,metadata:{}}];
function Harness({port,transcript=messages}:{port:BaselinePort;transcript?:ChatMsg[]}){const model=useBaselineChanges(port);return <BaselineChangesTool model={model} messages={transcript} scope={<span>Files edited by the agent since a checkpoint</span>}/>;}
const renderFixture=()=>{const f=setup();render(<StrictMode><Harness port={f.port}/></StrictMode>);return f;};
const loadPicker=async(f:ReturnType<typeof setup>,value:unknown=listing)=>{fireEvent.click(screen.getByRole("button",{name:"Load retained checkpoints"}));await act(async()=>f.reply(f.send.mock.calls.length-1,value));};
const choosePicker=async(f:ReturnType<typeof setup>,value:unknown=scope,diff:unknown=text)=>{fireEvent.change(screen.getByRole("combobox",{name:"Retained checkpoint"}),{target:{value:"0"}});await act(async()=>f.reply(f.send.mock.calls.length-1,value));if(f.send.mock.calls.at(-1)![0].type==="desktop/baselineDiff")await act(async()=>f.reply(f.send.mock.calls.length-1,diff));};
it("shows the actual user anchor, standing scope caveat and supplier facts in the shared screenshot frame",async()=>{
 const f=renderFixture();expect(f.send).not.toHaveBeenCalled();await loadPicker(f);expect(screen.getByRole("option",{name:/Before your message “Update the sidebar”/})).toBeInTheDocument();expect(f.send).toHaveBeenCalledTimes(1);
 await choosePicker(f);expect(screen.getByText(/Retained pre-edit content supplied by/)).toHaveTextContent("Before your message");expect(screen.getByText(/Changes made by commands or outside/)).toBeVisible();expect(screen.getByRole("button",{name:"Refresh checkpoint comparison"})).toBeInTheDocument();
 expect(screen.queryByText(/No HEAD commit|No uncommitted changes|Inspect this project's working tree/)).not.toBeInTheDocument();expect(screen.getByRole("button",{name:"Toggle changed files"})).toBeInTheDocument();
});
it("shows original partial counts/reasons instead of fabricating clean or Git facts",async()=>{
 const f=renderFixture();await loadPicker(f,{...listing,omitted:5});await choosePicker(f,{...scope,changes:[],unchanged:2,createdRemoved:1,uncompared:[{path:"linked.ts",reason:"Current file is a link."}],omitted:7,partial:true});
 expect(screen.getByText(/0 changed · 0 viewed · 2 unchanged · 1 created then removed · 1 not compared · 7 omitted/)).toBeInTheDocument();expect(screen.getByText("linked.ts — Current file is a link.")).toBeInTheDocument();expect(screen.getByText(/5 older checkpoints/)).toBeInTheDocument();expect(screen.getByText("No changes were confirmed in the inspected agent-edit files.")).toBeInTheDocument();expect(screen.queryByText(/No uncommitted changes/)).not.toBeInTheDocument();
});
it("distinguishes a genuinely empty checkpoint list from a comparison with no confirmed differences",async()=>{
 const f=renderFixture();await loadPicker(f,{...listing,checkpoints:[]});expect(screen.getByText("No retained checkpoints are listed for this conversation.")).toBeInTheDocument();expect(screen.getByRole("combobox",{name:"Retained checkpoint"})).toBeDisabled();expect(f.send).toHaveBeenCalledTimes(1);
});
it("keeps binary and oversize comparison states explicit without replacing the renderer",async()=>{
 const f=renderFixture();await loadPicker(f);await choosePicker(f,scope,{kind:"too-large"});expect(screen.getByText("This content is too large to compare.")).toBeInTheDocument();expect(screen.getByRole("button",{name:"Mark current file viewed"})).toBeDisabled();
});
it("labels absent anchors by real date only and never derives a turn number from seq",()=>{
 const label=checkpointLabel({...chosen,seq:90},new Map());expect(label).not.toMatch(/Before your message|Turn|90/);expect(label).not.toBe("Saved checkpoint");
 expect(checkpointLabel({...chosen,createdAt:"invalid"},new Map())).toBe("Saved checkpoint");
});
it("keeps the interactive reference visible under the narrow-workspace CSS override",async()=>{
 const f=renderFixture();await loadPicker(f);expect(screen.getByRole("combobox",{name:"Retained checkpoint"}).closest(".review-reference")).toHaveClass("is-control");
 fireEvent.change(screen.getByRole("combobox",{name:"Retained checkpoint"}),{target:{value:""}});expect(f.send).toHaveBeenCalledTimes(1);
});

it("refuses an old picker closure in the frame before React commits a new thread",async()=>{
 const f=hookFixture();await f.open();const oldChoose=f.result.current.choose;
 await act(async()=>{context("B");await oldChoose(chosen);});expect(f.send).toHaveBeenCalledTimes(1);expect(f.result.current.checkpoint).toBeUndefined();expect(f.result.current.result).toBeUndefined();
});
it("labels retained old evidence explicitly when a Refresh re-list fails",async()=>{
 const f=renderFixture();await loadPicker(f);await choosePicker(f);fireEvent.click(screen.getByRole("button",{name:"Refresh checkpoint comparison"}));await act(async()=>f.reply(3,undefined,"timeout"));
 expect(screen.getByText(/Showing the comparison from .*refresh didn't finish/)).toBeVisible();expect(screen.getByText("Controlled reason")).toBeVisible();expect(screen.getByRole("article",{name:/Changes to a.ts/})).toBeInTheDocument();expect(f.send).toHaveBeenCalledTimes(4);
});
it("does not call an unrequested or empty comparison large",async()=>{
 const f=renderFixture();expect(document.querySelector('.review-banner')).toHaveTextContent("Choose a retained checkpoint");expect(document.querySelector('.review-banner')).not.toHaveTextContent("large");
 await loadPicker(f);await choosePicker(f,{...scope,changes:[]});expect(document.querySelector('.review-banner')).toHaveTextContent("no changes confirmed");
});
it("adds an ellipsis when an actual user anchor label is shortened",async()=>{
 const f=setup();render(<Harness port={f.port} transcript={[{...messages[0]!,content:'X'.repeat(90)+'\nsecond line'}]}/>);await loadPicker(f);
 expect(screen.getByRole("option",{name:/Before your message “X+…”/})).toBeInTheDocument();expect(screen.queryByRole("option",{name:/second line/})).not.toBeInTheDocument();
});

it.each(["unavailable","busy"])("uses a neutral banner after a chosen checkpoint returns %s",async state=>{
 const f=renderFixture();await loadPicker(f);fireEvent.change(screen.getByRole("combobox",{name:"Retained checkpoint"}),{target:{value:"0"}});
 await act(async()=>{if(state==="unavailable")f.reply(1,{state:"unavailable",reason:"This checkpoint changed or was removed. Refresh."});else f.reply(1,undefined,"busy");});
 expect(document.querySelector('.review-banner')).toHaveTextContent("Agent-edit comparison unavailable.");expect(document.querySelector('.review-banner')).not.toHaveTextContent(/large|Choose a retained/);
 expect(screen.getByText(state==="unavailable"?"This checkpoint changed or was removed. Refresh.":"Controlled reason")).toBeVisible();expect(f.send).toHaveBeenCalledTimes(2);
});
