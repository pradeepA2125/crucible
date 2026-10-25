import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createBaselineClient } from "../desktop/baselineClient";
import DesktopApp, { desktopThreadStatus } from "../desktop/DesktopApp";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const thread = (id: string) => ({ threadId: id, workspacePath: "/project", title: `Conversation ${id}`, createdAt: "2026-10-21T10:00:00Z" });
const host = (data: unknown) => act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
let post: ReturnType<typeof vi.fn>;
beforeEach(async () => { window.innerWidth = 1440; post = (await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>; post.mockClear(); });
function ready() {
  host({ type: "desktop/project", name: "crucible-project" });
  host({ type: "renderThreadList", threads: [thread("a"), thread("b")], activeThreadId: "a" });
  host({ type: "clearThread", threadId: "a" });
  host({ type: "liveStatus", threadId: "a", status: null, turnActive: false, agentsRunning: 0 });
}
describe("single-project desktop shell", () => {
  it("uses shared thread rows and composer, filters titles, and locks navigation on legacy reconnected turns", async () => {
    render(<DesktopApp />); ready();
    expect(screen.getByRole("complementary", { name: "Conversations" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Search conversations" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back to history" })).toBeNull();
    const row = screen.getByRole("button", { name: /Conversation b/ }); await act(async () => { fireEvent.click(row); });
    expect(post).toHaveBeenCalledWith({ type: "switchThread", threadId: "b" });
    post.mockClear(); host({ type: "liveStatus", threadId: "a", status: null, turnActive: true, agentsRunning: 1 });
    expect(screen.getByRole("button", { name: "New conversation" })).toBeDisabled();
    fireEvent.click(row); expect(post).not.toHaveBeenCalledWith(expect.objectContaining({ type: "switchThread" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop all agents" })); expect(post).toHaveBeenCalledWith({ type: "stopAllAgents" });
    fireEvent.change(screen.getByRole("textbox", { name: "Search conversations" }), { target: { value: "nothing" } });
    expect(screen.getByText("No conversations match.")).toBeInTheDocument();
  });
  it("keeps inspection toggles separate from work, retains the composer while expanded, and keeps the root dialog", () => {
    render(<DesktopApp />); ready();
    const input = screen.getByRole("textbox", { name: "Chat input" });
    fireEvent.change(input, { target: { value: "retained draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Open workspace" }));
    expect(screen.getByRole("complementary", { name: "Workspace" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand workspace" }));
    expect(input).toHaveValue("retained draft");
    fireEvent.click(screen.getByRole("button", { name: "Restore conversation" }));
    fireEvent.click(screen.getByRole("button", { name: "Hide workspace" }));
    expect(input).toHaveValue("retained draft");
    expect(post.mock.calls.some(([message]) => message.type === "stopAgent" || message.type === "disbandTeam" || message.type === "stopTurn")).toBe(false);
    host({ type: "confirmFullAccessFirstSend", requestId: "question", workspace: "project" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(post).toHaveBeenCalledWith({ type: "fullAccessFirstSendChoice", requestId: "question", choice: "cancel" });
  });
  it("does not invent running or completion for older backend summaries", () => {
    expect(desktopThreadStatus(thread("a"), false, false, 0)).toBeUndefined();
    expect(desktopThreadStatus({ ...thread("a"), turnActive: false }, false, false, 0)).toBeUndefined();
    expect(desktopThreadStatus({ ...thread("a"), turnActive: true }, false, false, 0)).toBe("running");
    expect(desktopThreadStatus({ ...thread("a"), pendingGates: 1 }, false, false, 0)).toBe("review");
  });
  it("keeps navigation available when live status never arrives and offers a retry", async () => {
    vi.useFakeTimers();
    try {
      render(<DesktopApp />);
      host({ type: "renderThreadList", threads: [thread("a"), thread("b")], activeThreadId: "a" });
      host({ type: "clearThread", threadId: "a" });
      expect(screen.getByRole("button", { name: "New conversation" })).toBeEnabled();
      await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Conversation b/ })); });
      expect(post).toHaveBeenCalledWith({ type: "switchThread", threadId: "b" });
      act(() => { vi.advanceTimersByTime(8000); });
      expect(screen.getByText(/Conversation status is unavailable/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(post).toHaveBeenCalledWith({ type: "switchThread", threadId: "a" });
      expect(screen.getByText("Opening your conversation…")).toBeInTheDocument();
    } finally { vi.useRealTimers(); }
  });
  it("keeps approvals discoverable when the conversation is hidden by inspection", () => {
    render(<DesktopApp />); ready();
    fireEvent.click(screen.getByRole("button", { name: "Open workspace" }));
    fireEvent.click(screen.getByRole("button", { name: "Expand workspace" }));
    host({ type: "renderLiveGates", gates: [{ gateId: "g", kind: "command", taskId: "t", payload: { command: "pwd" }, agent: null }] });
    fireEvent.click(screen.getByRole("button", { name: "Approval needed · Show conversation" }));
    expect(screen.getByRole("textbox", { name: "Chat input" })).toBeVisible();
    expect(screen.queryByRole("complementary", { name: "Workspace" })).toBeNull();
  });

});

it("wires the actual baseline scope above the shared composer and preserves it across workspace toggles",async()=>{
 const send=vi.fn(),port=createBaselineClient(send),emit=(data:unknown)=>act(()=>window.dispatchEvent(new MessageEvent("message",{data,source:window,origin:"app://app"})));
 render(<DesktopApp baselineStore={port}/>);ready();emit({type:"desktop/baselineContext",viewId:"view",projectId:"project",threadId:"a"});
 const input=screen.getByRole("textbox",{name:"Chat input"});fireEvent.change(input,{target:{value:"keep this draft"}});
 fireEvent.click(screen.getByRole("button",{name:"Open workspace"}));fireEvent.click(screen.getByRole("button",{name:"New workspace tab"}));fireEvent.click(screen.getByRole("button",{name:"Changes"}));
 expect(send).not.toHaveBeenCalled();fireEvent.change(screen.getByRole("combobox",{name:"Changes scope"}),{target:{value:"baseline"}});expect(send).toHaveBeenCalledTimes(1);
 const checkpoint={seq:0,anchorMessageId:"anchor",createdAt:"2026-10-22T12:00:00Z"};
 const respond=(index:number,result:unknown)=>emit({type:"desktop/baselineResult",requestId:send.mock.calls[index]![0].requestId,viewId:"view",projectId:"project",threadId:"a",operation:index===0?"list":"scope",outcome:{status:"ok",result}});
 await act(async()=>respond(0,{state:"ready",checkpoints:[checkpoint],omitted:0}));expect(send).toHaveBeenCalledTimes(1);fireEvent.change(screen.getByRole("combobox",{name:"Retained checkpoint"}),{target:{value:"0"}});
 await act(async()=>respond(1,{state:"ready",generation:"12345678-1234-4234-8234-123456789abc",checkpoint,changes:[],unchanged:1,createdRemoved:0,uncompared:[],omitted:0,partial:false}));
 expect(screen.getByText("No agent-edit content differences were confirmed.")).toBeInTheDocument();expect(input).toHaveValue("keep this draft");
 fireEvent.click(screen.getByRole("button",{name:"Hide workspace"}));fireEvent.click(screen.getByRole("button",{name:"Open workspace"}));expect(screen.getByText("No agent-edit content differences were confirmed.")).toBeInTheDocument();expect(send).toHaveBeenCalledTimes(2);expect(input).toHaveValue("keep this draft");port.dispose();
});


it("allows guarded busy browsing and draft editing while submission is disabled", async () => {
  render(<DesktopApp />); ready();
  host({ type: "historyState", history: { threadId: "a", phase: "current", cached: true, navigationSafe: true, workBlocked: "Work is running in this project." } });
  host({ type: "liveStatus", threadId: "a", status: null, turnActive: true });
  expect(screen.getByRole("textbox", { name: "Chat input" })).toBeEnabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Chat input" }), { target: { value: "draft while A runs" } });
  expect(screen.getByRole("button", { name: "New conversation" })).toBeEnabled();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Conversation b/ })); });
  expect(post).toHaveBeenCalledWith({ type: "switchThread", threadId: "b" });
  expect(post).not.toHaveBeenCalledWith({ type: "stopTurn" });
});
