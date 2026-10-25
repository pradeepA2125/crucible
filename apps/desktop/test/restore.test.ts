import { describe,it,expect } from "vitest";
import { restoreRequest,restoreReply,readRestoreResponse } from "../src/restore-protocol.js";
import { refusalMessages } from "../src/refusal.js";
const requestId="e52441bc-4a5f-4283-947d-be80d582b0fc";
describe("file restore boundary",()=>{
 it("refuses extra authority and incomplete action fields",()=>{
  expect(restoreRequest.safeParse({type:"desktop/restore",requestId,threadId:"t",action:{operation:"apply",token:requestId,path:"/outside"}}).success).toBe(false);
  expect(restoreRequest.safeParse({type:"desktop/restore",requestId,threadId:"t",action:{operation:"preview",path:"../escape"}}).success).toBe(false);
 });
 it("correlates recovery refusal without reviving composer submission",()=>{
  const replies=refusalMessages({type:"desktop/restore",requestId},"closing","p",{viewId:"v",threadId:"t"});
  expect(replies).toHaveLength(1);expect(restoreReply.parse(replies[0]).error).toBe("closing");
 });
 it("bounds replies and preserves backend conflict copy",async()=>{
  await expect(readRestoreResponse(new Response(JSON.stringify({detail:"File changed"}),{status:409}))).rejects.toThrow("File changed");
  await expect(readRestoreResponse(new Response("x".repeat(1024*1024+1)))).rejects.toThrow("too much");
  expect(await readRestoreResponse(new Response(JSON.stringify({kind:"history",entries:[]})))).toEqual({kind:"history",entries:[]});
 });
});

import { readFileSync } from "node:fs";
import { restoreAction } from "../src/restore-protocol.js";
import { vi } from "vitest";
/** Exercise the actual host admission function with held ownership/HTTP boundaries. */
function hostBoundary(readAccess:()=>Promise<unknown>,fetcher:(...args:unknown[])=>Promise<Response>){
 const source=readFileSync(new URL("../src/host.ts",import.meta.url),"utf8"),start=source.indexOf("  const fileRestore=async"),end=source.indexOf("  return { projectId, runtime, activity, canDetach, fileRestore",start);
 const body=source.slice(start,end).replace("threadId:string,raw:RestoreAction","threadId,raw");
 const dependencies={restoreAction,readRestoreResponse,ports:{localTerminalBusy:()=>false,viewIdentity:()=>1},controller:{memoryThreadId:()=>"t"},ownerFor:()=>"owner",readAccess,gate:{authedFetchFor:()=>fetcher}};
 return new Function("d",`let disposed=false,restoring=false,backendURL="http://127.0.0.1:12345",backendGeneration=1;const canDetach=()=>!restoring;const {${Object.keys(dependencies).join(",")}}=d;${body};return{fileRestore,busy:()=>restoring,restart:()=>backendGeneration++};`)(dependencies);
}
it("actual host keeps recovery admission exclusive and supplies current owner process authority",async()=>{
 let release!:(v:unknown)=>void;const access=new Promise(r=>release=r),fetcher=vi.fn(async()=>new Response(JSON.stringify({kind:"history",entries:[]}))),h=hostBoundary(()=>access,fetcher);
 const pending=h.fileRestore("t",{operation:"history"});expect(h.busy()).toBe(true);await expect(h.fileRestore("t",{operation:"history"})).rejects.toThrow("Finish project actions");
 release({role:"owner",process_id:"current-owner"});await pending;expect(JSON.parse((fetcher.mock.calls as unknown as [string,RequestInit][])[0]![1].body as string)).toEqual({operation:"history",process_id:"current-owner"});expect(h.busy()).toBe(false);
});
it("actual host refuses an observer or restart during ownership preflight without issuing a write",async()=>{
 for(const restart of [false,true]){let release!:(v:unknown)=>void;const access=new Promise(r=>release=r),fetcher=vi.fn(),h=hostBoundary(()=>access,fetcher),pending=h.fileRestore("t",{operation:"history"});if(restart)h.restart();release({role:restart?"owner":"observer",process_id:"current"});await expect(pending).rejects.toThrow("current controlling");expect(fetcher).not.toHaveBeenCalled();expect(h.busy()).toBe(false);}
});

import {transformSync} from "esbuild";
import {terminalRequest,terminalResult} from "../src/terminal-protocol.js";
it("actual main terminal branch acknowledges a recovery refusal while retaining the no-launch fence",async()=>{
 const source=readFileSync(new URL("../src/main.ts",import.meta.url),"utf8"),start=source.indexOf('    if(payload.type==="desktop/terminal"){'),end=source.indexOf('    if(payload.type==="desktop/projectsRead")',start),branch=source.slice(start,end),post=vi.fn();
 const deps={payload:{type:"desktop/terminal",requestId:"12545678-1234-4234-8234-123456789abc",operation:"start",cols:80,rows:24},event:{senderFrame:{url:"app://app/desktop-app.html"}},requestHost:{restoreBusy:()=>true,terminalLease:async()=>{throw new Error("Wait for file recovery to finish before launching Your shell.");}},context:{projectId:"p",processId:"process"},requestCurrent:()=>true,closing:false,terminalRequest,terminalResult,registry:{get:()=>({name:"Disposable",root:"/private/tmp/unused"})},post};
 const js=transformSync(`async function actual(){let terminalLaunchPending=false;${branch}}`,{loader:"ts",target:"es2022"}).code;
 const run=new Function("d",`const {${Object.keys(deps).join(",")}}=d;${js};return actual;`)(deps);await run();expect(post).toHaveBeenCalledWith(expect.objectContaining({type:"desktop/terminalResult",requestId:deps.payload.requestId,error:expect.stringContaining("file recovery")}));
});
