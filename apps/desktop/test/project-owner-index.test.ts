import {expect,it,vi} from "vitest";
import {requestProjectIndex} from "../src/host.js";
import {BackendGate} from "../../vscode-extension/src/backend-auth/backend-gate.js";
import {newOwner,registerOwner,forgetOwner} from "../../vscode-extension/src/runtime/project-owner.js";
import {healthProof,healthBound} from "../../vscode-extension/src/runtime/probe-health.js";
it("AXON's actual build adapter carries bearer and current owner after bound process proof",async()=>{
 const base="http://127.0.0.1:54328",owner=newOwner("Desktop","/project");owner.pid=42;registerOwner(54328,owner);
 const fetch=vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes("/health?")){const nonce=new URL(url).searchParams.get("nonce")!;return new Response(JSON.stringify({pid:42,proof:healthProof("token",nonce),bound:healthBound("token",nonce,42,"/project")}));}
  const headers=new Headers(init?.headers);expect(headers.get("authorization")).toBe("Bearer token");expect(headers.get("x-crucible-owner")).toBe(owner.key);
  if(url.endsWith("/project/session"))return new Response(JSON.stringify({protocol:1,workspace:"/project",pid:42,process_id:"p",client_id:owner.clientId,client_name:"Desktop",role:"owner",trusted:false}));
  expect(JSON.parse(init!.body as string)).toEqual({workspace_path:"/project"});return new Response("{}",{status:202});
 });
 const gate=new BackendGate({fetch,probe:async()=>"authed",readToken:()=>"token",report:vi.fn(),now:Date.now});
 try{await requestProjectIndex(gate,base,"/project");expect(fetch).toHaveBeenCalledTimes(3);forgetOwner(54328,owner);fetch.mockClear();await expect(requestProjectIndex(gate,base,"/project")).rejects.toThrow("observer");expect(fetch).not.toHaveBeenCalled();}finally{forgetOwner(54328,owner);}
});
