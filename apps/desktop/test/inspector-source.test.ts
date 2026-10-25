import { expect,it } from "vitest";
import { mkdtempSync,mkdirSync,writeFileSync,symlinkSync,rmSync,realpathSync } from "node:fs";
import {tmpdir} from "node:os";import{join}from"node:path";
import { readInspectorSnapshot,inspectorGraph } from "../src/inspector-source.js";
import { snap,fileNode } from "../../vscode-extension/test/graph-fixtures.js";
it("reuses the real graph model and refuses malformed/outside-root snapshots",()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),"cp7-axon-")));mkdirSync(join(root,".crucible"));const file=join(root,".crucible/index-snapshot.json");
 try{
  writeFileSync(file,JSON.stringify({...snap([fileNode("src/a.ts")],[]),workspace_root:root}));expect(JSON.parse(readInspectorSnapshot(root)).graph.nodes).toHaveLength(1);
  const graph=inspectorGraph(root,async()=>{},async()=>{});expect(graph.loadModel().stars).toHaveLength(1);
  writeFileSync(file,JSON.stringify({workspace_root:root,graph:{nodes:{},edges:[]}}));expect(()=>readInspectorSnapshot(root)).toThrow("malformed");
  rmSync(file);symlinkSync("/etc/hosts",file);expect(()=>readInspectorSnapshot(root)).toThrow("inside");
 }finally{rmSync(root,{recursive:true,force:true});}
});

import { secureSession } from "../src/native-security.js";
it("allows the packaged graph layout worker only in the graph document",async()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),"cp7-csp-")));let handler!:(r:Request)=>Promise<Response>;
 try {
  writeFileSync(join(root,"graph.html"),"graph");writeFileSync(join(root,"memory.html"),"memory");
  await secureSession({setPermissionRequestHandler(){},setPermissionCheckHandler(){},protocol:{handle(_scheme:string,fn:typeof handler){handler=fn;}}} as never,root);
  expect((await handler(new Request("app://app/graph.html"))).headers.get("Content-Security-Policy")).toContain("worker-src 'self'");
  const memory=(await handler(new Request("app://app/memory.html"))).headers.get("Content-Security-Policy");expect(memory).toContain("worker-src 'none'");expect(memory).toContain("connect-src 'none'");expect(memory).toContain("frame-src 'none'");
 }finally{rmSync(root,{recursive:true,force:true});}
});
