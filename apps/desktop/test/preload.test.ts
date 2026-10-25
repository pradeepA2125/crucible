import { beforeAll, describe, expect, it, vi } from "vitest";
const fake = vi.hoisted(() => ({ expose: vi.fn(), invoke: vi.fn(), on: vi.fn(), post: vi.fn() }));
vi.mock("electron", () => ({ contextBridge: { exposeInMainWorld: fake.expose }, ipcRenderer: { invoke: fake.invoke, on: fake.on } }));
beforeAll(async () => {
  vi.stubGlobal("window", { postMessage: fake.post, addEventListener: vi.fn() });
  fake.invoke.mockResolvedValue({ v: 1, clientId: "client", viewId: "view", projectId: "project", threadId: null, processId: null });
  await import("../src/preload.js");
});
const flush = async () => { await new Promise(resolve => setTimeout(resolve, 0)); };
describe("isolated compatibility bridge", () => {
  it("exposes only acquireVsCodeApi with the existing three methods", () => {
    expect(fake.expose.mock.calls.map(call => call[0])).toEqual(["acquireVsCodeApi"]);
    expect(Object.keys(fake.expose.mock.calls[0]![1]())).toEqual(["postMessage", "getState", "setState"]);
  });
  it("never throws a schema refusal into React and returns the complete draft visibly", async () => {
    fake.post.mockClear();
    const api = fake.expose.mock.calls[0]![1]();
    const raw = { type: "sendMessage", text: "expanded", originalDraftText: "my draft", draftId: "draft", imageAttachments: [], mentionedPaths: ["a.ts"], unexpected: true };
    expect(() => api.postMessage(raw)).not.toThrow(); await flush();
    const replies = fake.post.mock.calls.map(call => call[0]);
    expect(replies).toContainEqual({ type: "draftRecovery", text: "my draft", draftId: "draft", imageAttachments: [], mentionedPaths: ["a.ts"] });
    expect(replies.some(reply => reply.type === "appendMessage" && reply.message.type === "notice")).toBe(true);
  });
  it("resets a refused decision and re-enables input after IPC failure", async () => {
    fake.post.mockClear(); fake.invoke.mockRejectedValueOnce(new Error("disconnected"));
    fake.expose.mock.calls[0]![1]().postMessage({ type: "modeDecision", threadId: "thread", mode: "implement" }); await flush();
    expect(fake.post.mock.calls.map(call => call[0])).toContainEqual({ type: "decisionRetry", threadId: "thread", kind: "mode", gateId: undefined });
    expect(fake.post.mock.calls.map(call => call[0])).toContainEqual({ type: "setInputEnabled", enabled: true });
  });
});


it("keeps a stale baseline refusal stamped with its request context and never changes chat input",async()=>{
 const api=fake.expose.mock.calls[0]![1](), listener=fake.on.mock.calls.find(call=>call[0]==="crucible:event")![1];
 const original={v:1,clientId:"client",viewId:"view",projectId:"project",threadId:"thread-A",processId:"process"};
 listener({}, {context:original,payload:{type:"clearThread",threadId:"thread-A"}});fake.post.mockClear();
 let finish!:(value:unknown)=>void;fake.invoke.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
 const requestId="12345678-1234-4234-8234-123456789abc";
 api.postMessage({type:"desktop/baselineList",requestId,threadId:"thread-A"});await flush();
 expect(fake.invoke.mock.calls.at(-1)![1]).toMatchObject({viewId:"view",threadId:"thread-A",payload:{requestId}});
 listener({}, {context:{...original,threadId:"thread-B"},payload:{type:"clearThread",threadId:"thread-B"}});fake.post.mockClear();
 finish({error:"Stale desktop context. Reload this view before retrying."});await flush();
 expect(fake.post.mock.calls.map(call=>call[0])).toEqual([expect.objectContaining({type:"desktop/baselineResult",requestId,viewId:"view",projectId:"project",threadId:"thread-A",operation:"list",outcome:{status:"error",message:expect.any(String)}})]);
});
it("turns a malformed baseline payload into its own typed refusal",async()=>{
 fake.post.mockClear();const api=fake.expose.mock.calls[0]![1]();
 api.postMessage({type:"desktop/baselineScope",requestId:"12345678-1234-4234-8234-123456789abc",threadId:"thread-B",checkpoint:{seq:-1}});await flush();
 expect(fake.post.mock.calls.map(call=>call[0])).toEqual([
  {type:"desktop/baselineContext",viewId:"view",projectId:"project",threadId:"thread-B"},
  expect.objectContaining({type:"desktop/baselineResult",operation:"scope",outcome:{status:"error",message:expect.any(String)}}),
 ]);
});

it("broadcasts only authoritative baseline identity before host data and initial ready dispatch",async()=>{
 fake.post.mockClear();const listener=fake.on.mock.calls.find(call=>call[0]==="crucible:event")![1];
 const context={v:1,clientId:"client",viewId:"view",projectId:"project",threadId:null,processId:"private-process"};
 listener({}, {context,payload:{type:"renderThreadList",threads:[],activeThreadId:null}});
 expect(fake.post.mock.calls.map(call=>call[0])).toEqual([{type:"desktop/baselineContext",viewId:"view",projectId:"project",threadId:null},{type:"renderThreadList",threads:[],activeThreadId:null}]);
 fake.post.mockClear();fake.expose.mock.calls[0]![1]().postMessage({type:"webviewReady"});await flush();
 expect(fake.post.mock.calls[0]![0]).toEqual({type:"desktop/baselineContext",viewId:"view",projectId:"project",threadId:null});
 expect(JSON.stringify(fake.post.mock.calls)).not.toMatch(/private-client|private-process/);
});

it("drops foreign project/view events instead of changing the renderer identity",()=>{
 fake.post.mockClear();const listener=fake.on.mock.calls.find(call=>call[0]==="crucible:event")![1];
 listener({}, {context:{v:1,clientId:"client",viewId:"stale-view",projectId:"foreign",threadId:"other",processId:null},payload:{type:"settings/state",state:{provider:"foreign"}}});expect(fake.post).not.toHaveBeenCalled();
});
