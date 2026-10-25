import { createHash } from "node:crypto";
import { createOwnedTree, type OwnedTree } from "./terminal-process-tree.js";
import { devNull } from "node:os";
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync, existsSync, renameSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import { basename, join } from "node:path";
import { pathToFileURL } from "node:url";
import { RuntimeInstaller, type InstallerDeps, type ComponentProgress, type InstallResult, type ExecResult } from "../../vscode-extension/src/runtime/installer.js";
import { platformKey, type RuntimeManifest } from "../../vscode-extension/src/runtime/manifest.js";
// Fixed trusted supervisor, never interpolated command text. It holds the verified
// private-group root alive until the main process has settled all descendants.
const SUPERVISOR = String.raw`
const {spawn}=require('node:child_process');
let started=false;
process.on('message',m=>{
 if(started || !m || m.type!=='start')return;started=true;
 const child=spawn(m.command,m.args,{cwd:m.cwd,env:m.env,stdio:['ignore','inherit','inherit']});
 child.on('error',()=>process.send?.({type:'complete',code:1,failed:true}));
 child.on('close',code=>process.send?.({type:'complete',code:code??1}));
});
process.on('disconnect',()=>{
 if(process.platform==='win32')spawn('taskkill',['/PID',String(process.pid),'/T','/F'],{stdio:'ignore'}).on('error',()=>process.exit(1));
 else {try{process.kill(-process.pid,'SIGKILL');}catch{process.exit(1);}}
});
`;
export function installerEnvironment(root:string,source:NodeJS.ProcessEnv):Record<string,string>{
 return {...provisionEnvironment(source),UV_CACHE_DIR:join(root,"cache","uv"),UV_PYTHON_INSTALL_DIR:join(root,"python"),UV_PYTHON_PREFERENCE:"only-managed",UV_NO_CONFIG:"1",UV_NATIVE_TLS:"true",UV_INDEX_URL:"https://pypi.org/simple",PIP_CONFIG_FILE:devNull,
 NPM_CONFIG_CACHE:join(root,"cache","npm"),NPM_CONFIG_USERCONFIG:join(root,"npm-user.conf"),NPM_CONFIG_GLOBALCONFIG:join(root,"npm-global.conf"),NPM_CONFIG_REGISTRY:"https://registry.npmjs.org",NPM_CONFIG_IGNORE_SCRIPTS:"true",NPM_CONFIG_AUDIT:"false",NPM_CONFIG_FUND:"false"};
}
export async function downloadArtifact(url:string,signal:AbortSignal):Promise<Buffer>{
 const seen=new Set<string>();let current=new URL(url);
 for(let hops=0;hops<=5;hops++){
  signal.throwIfAborted();if(current.protocol!=="https:"||current.username||current.password||current.hash||seen.has(current.href))throw new Error("Unsafe or looping runtime redirect.");seen.add(current.href);
  const response=await fetch(current.href,{redirect:"manual",signal});
  if([301,302,303,307,308].includes(response.status)){const location=response.headers.get("location");await response.body?.cancel();if(!location||hops===5)throw new Error("Runtime redirect limit exceeded.");current=new URL(location,current);continue;}
  if(!response.ok||!response.body)throw new Error(`Runtime download failed (${response.status}).`);
  const chunks:Uint8Array[]=[];let size=0;for await(const chunk of response.body){size+=chunk.length;if(size>512*1024*1024)throw new Error("Runtime artifact exceeds the download limit.");signal.throwIfAborted();chunks.push(chunk);}return Buffer.concat(chunks);
 }throw new Error("Runtime redirect limit exceeded.");
}
const COMPONENTS=["uv","agentd","indexer","ripgrep","rust-analyzer","gopls","jre","jdtls","lsps"] as const;
/** Production provisioning never falls back to an unpinned PyPI backend or a dev checkout. */
export function pinnedRuntime(raw:unknown, platform=platformKey()):RuntimeManifest {
 const m=raw as RuntimeManifest;
 if(!m || m.manifestVersion!==1 || typeof m.releaseTag!=="string" || !m.releaseTag || /dev|placeholder|unpinned/i.test(m.releaseTag))throw new Error("This build has no pinned runtime release. Install a release build with its runtime manifest.");
 for(const id of COMPONENTS){const spec=m.components?.[id];if(!spec||typeof spec.version!=="string"||!spec.version)throw new Error(`Runtime manifest is missing ${id}.`);
  if(id==="lsps"){if(!spec.npmPackages?.length||spec.npmPackages.some(p=>!/^(@[a-z0-9-]+\/)?[a-z0-9-]+@\d+\.\d+\.\d+([+-][a-z0-9.-]+)?$/i.test(p)))throw new Error("Language server packages must have exact versions.");continue;}
  const key=id==="agentd"||id==="jdtls"?"any":platform;const url=spec.urls?.[key],sha=spec.sha256?.[key];
  if(!url||!sha||!/^[a-f0-9]{64}$/i.test(sha))throw new Error(`Runtime manifest has no pinned ${platform} artifact for ${id}.`);
  const parsed=new URL(url);if(parsed.protocol!=="https:"||parsed.username||parsed.password||parsed.hash)throw new Error("Runtime artifacts require credential-free HTTPS URLs.");
  if(id==="agentd"&&!parsed.pathname.endsWith(".whl"))throw new Error("Backend must be a pinned wheel.");
 }
 return structuredClone(m);
}
export function provisionEnvironment(source:NodeJS.ProcessEnv):Record<string,string> {
 const env:Record<string,string>={};for(const k of ["PATH","HOME","USERPROFILE","SYSTEMROOT","SystemRoot","WINDIR","TEMP","TMP","TMPDIR","COMSPEC","PATHEXT","LANG","LC_ALL","SSL_CERT_FILE","SSL_CERT_DIR"]){if(source[k])env[k]=source[k]!;}
 return env;
}
export class DesktopProvisioner {
 private commands=new Set<{stop():Promise<void>}>();
 private current:{abort:AbortController;done:Promise<InstallResult>}|undefined;
 get busy(){return !!this.current;}
 constructor(private readonly allowed:()=>void,private readonly env=provisionEnvironment(process.env),private readonly assets?:string){}
 install(deps:InstallerDeps, progress:(p:ComponentProgress)=>void):Promise<InstallResult> {
  if(this.current)throw new Error("Runtime installation is already in progress.");
  this.allowed();if(deps.devSourcePath)throw new Error("Packaged runtime installation cannot use a development source.");
  const manifest=pinnedRuntime(deps.manifest,deps.platform),abort=new AbortController();
  // Install owns one lease until downloads, extraction and child processes actually settle.
  const done=this.run(deps,manifest,progress,abort.signal);this.current={abort,done};
  void done.finally(()=>{if(this.current?.done===done && !this.commands.size)this.current=undefined;}).catch(()=>{});return done;
 }
 private async run(deps:InstallerDeps,manifest:RuntimeManifest,progress:(p:ComponentProgress)=>void,signal:AbortSignal):Promise<InstallResult>{
  this.allowed();mkdirSync(deps.runtimeDir,{recursive:true,mode:0o700});if(lstatSync(deps.runtimeDir).isSymbolicLink())throw new Error("Runtime directory must not be a symbolic link.");
  const check=()=>signal.throwIfAborted();
  const download=async(url:string)=>{check();
   if(this.assets && existsSync(join(this.assets,"runtime-assets.json"))){const entries=JSON.parse(readFileSync(join(this.assets,"runtime-assets.json"),"utf8")) as Record<string,{file:string;sha256:string}>;const entry=entries[url];if(!entry || !/^[a-zA-Z0-9_.+-]+$/.test(entry.file))throw new Error("A bundled runtime artifact is missing.");const directory=join(this.assets,"runtime-assets"),file=join(directory,entry.file);if(realpathSync(directory)!==join(realpathSync(this.assets),"runtime-assets") || lstatSync(file).isSymbolicLink() || !lstatSync(file).isFile() || lstatSync(file).size>512*1024*1024)throw new Error("Bundled runtime artifact is unsafe.");const bytes=readFileSync(file);if(createHash("sha256").update(bytes).digest("hex")!==entry.sha256)throw new Error("Bundled runtime artifact checksum mismatch.");return bytes;}
   return downloadArtifact(url,signal);};
  const environment=installerEnvironment(deps.runtimeDir,this.env);
  for(const name of ["npm-user.conf","npm-global.conf"])writeFileSync(join(deps.runtimeDir,name),"",{mode:0o600});

  // RuntimeInstaller verifies binary/archive artifacts; verify the wheel too before uv sees it.
  const wheel=manifest.components.agentd,url=wheel.urls!.any!,data=await download(url);
  if(createHash("sha256").update(data).digest("hex")!==wheel.sha256!.any!.toLowerCase())throw new Error("Backend wheel checksum mismatch.");
  const cache=join(deps.runtimeDir,"artifacts");mkdirSync(cache,{recursive:true,mode:0o700});const name=basename(new URL(url).pathname);
  if(!/^[a-zA-Z0-9_.+-]+\.whl$/.test(name))throw new Error("Backend wheel filename is invalid.");
  const path=join(cache,name);writeFileSync(path,data,{mode:0o600});wheel.urls!.any=pathToFileURL(path).href;check();this.allowed();
  const marker=join(deps.runtimeDir,"runtime.json");if(existsSync(marker))renameSync(marker,join(deps.runtimeDir,"runtime.previous.json"));
  const run=(cmd:string,args:string[],options?:{cwd?:string})=>this.exec(cmd,args,options?.cwd??deps.runtimeDir,signal,environment);
  const result=await new RuntimeInstaller({...deps,manifest,download,exec:run,hasNode:async()=>{const r=await run("node",["--version"]);return r.code===0;},extract:async(...args)=>{check();await deps.extract(...args);check();}}).installAll(p=>{check();progress(p);});
  check();return result;
 }
 private exec(command:string,args:string[],cwd:string|undefined,signal:AbortSignal,environment=this.env):Promise<ExecResult>{
  signal.throwIfAborted();
  return new Promise((resolve,reject)=>{
   const worker=spawn(process.execPath,["-e",SUPERVISOR],{env:{...environment,ELECTRON_RUN_AS_NODE:"1"},detached:process.platform!=="win32",stdio:["ignore","pipe","pipe","ipc"]});
   let stdout="",stderr="",closed=false,finishing=false,stopReason:"cancel"|"deadline"|"failure"|undefined,code=1;
   let closeResolve!:()=>void;const exited=new Promise<void>(r=>closeResolve=r);
   const tree:Promise<OwnedTree|undefined>=process.platform!=="win32" && worker.pid?createOwnedTree(worker.pid,process.pid,undefined,undefined,true):Promise.resolve(undefined);
   let refresh:ReturnType<typeof setInterval>|undefined,retiring:Promise<void>|undefined;
   const retire=()=>retiring??=(async()=>{
    clearInterval(refresh);
    const owned=await tree;
    if(owned){await owned.refresh();await owned.terminate();}
    else if(!closed && worker.pid){await new Promise<void>((ok,no)=>{const task=spawn("taskkill",["/PID",String(worker.pid),"/T","/F"],{stdio:"ignore",env:environment});task.on("error",no);task.on("close",value=>value===0?ok():no(new Error("Installer descendant cleanup could not be verified.")));});}
    await exited;this.commands.delete(record);
   })().finally(()=>{retiring=undefined;});
   const record={stop:retire};this.commands.add(record);
   const settle=async(reason?:typeof stopReason)=>{
    if(finishing)return;finishing=true;stopReason=reason??stopReason;clearTimeout(deadline);signal.removeEventListener("abort",cancel);
    try{await retire();if(stopReason==="cancel")reject(new Error("Runtime installation was cancelled; reopen Setup to retry."));else resolve({code:stopReason?1:code,stdout,stderr:stopReason==="deadline"?"Runtime command deadline elapsed.":stderr});}
    catch{reject(new Error("Installer cleanup could not be verified. Its installation slot is retained; retry quitting."));}
   };
   const cancel=()=>{void settle("cancel");};
   const deadline=setTimeout(()=>{void settle("deadline");},15*60*1000);
   signal.addEventListener("abort",cancel,{once:true});
   worker.stdout?.on("data",v=>{stdout=(stdout+String(v)).slice(-65536);});worker.stderr?.on("data",v=>{stderr=(stderr+String(v)).slice(-65536);});
   worker.on("message",raw=>{const m=raw as {type?:string;code?:number};if(m.type==="complete" && Number.isInteger(m.code)){code=m.code!;void settle();}});
   worker.on("error",()=>{closed=true;closeResolve();void settle("failure");});
   worker.on("close",()=>{closed=true;closeResolve();if(!finishing)void settle("failure");});
   void tree.then(owned=>{
    if(finishing)return;
    if(owned)refresh=setInterval(()=>{void owned.refresh().catch(()=>{void settle("failure");});},50);
    worker.send({type:"start",command,args,cwd,env:environment},error=>{if(error)void settle("failure");});
   }).catch(async()=>{
    // No command is sent until identity capture succeeds, so this failed-start
    // worker has no command descendants. Kill only its still-owned child handle.
    if(!closed)worker.kill("SIGKILL");await exited;this.commands.delete(record);clearTimeout(deadline);clearInterval(refresh);signal.removeEventListener("abort",cancel);reject(new Error("Installer worker ownership could not be verified; no command started."));
   });
   if(signal.aborted)cancel();
  });
 }
 async dispose():Promise<void>{
  const active=this.current;active?.abort.abort();
  await Promise.all([...this.commands].map(command=>command.stop()));
  if(active)await active.done.catch(()=>{});
  if(!this.commands.size)this.current=undefined;
 }
}
