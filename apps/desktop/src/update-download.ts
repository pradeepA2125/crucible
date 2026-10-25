import {createHash} from "node:crypto";
import {open,rm} from "node:fs/promises";
import {REPOSITORY,MAX_PACKAGE} from "./update-manifest.js";
const CDN=new Set(["release-assets.githubusercontent.com","objects.githubusercontent.com"]);
function allowed(raw:string,initial:string){const u=new URL(raw),first=new URL(initial);if(u.protocol!=="https:"||u.username||u.password||u.hash||u.port)throw Error("Unsafe update download address.");
 if(u.origin===first.origin&&u.pathname===first.pathname)return u;
 if(CDN.has(u.hostname))return u;
 throw Error("Update download redirected outside GitHub release storage.");}
export async function githubResponse(initial:string,signal:AbortSignal,fetcher:typeof fetch=fetch){
 const first=new URL(initial);if(first.protocol!=="https:"||first.search||first.hash||first.username||first.password||first.port||!(initial===`https://api.github.com/repos/${REPOSITORY}/releases/latest`||initial.startsWith(`https://github.com/${REPOSITORY}/releases/download/`)))throw Error("Only the configured GitHub repository can provide updates.");
 let url=initial;const seen=new Set<string>();for(let i=0;i<=5;i++){signal.throwIfAborted();allowed(url,initial);if(seen.has(url))throw Error("Update redirect loop.");seen.add(url);
 const r=await fetcher(url,{signal,redirect:"manual",credentials:"omit",headers:{Accept:"application/vnd.github+json","User-Agent":"Crucible-desktop-update"}});
 if(r.status>=300&&r.status<400){const next=r.headers.get("location");await r.body?.cancel();if(!next||i===5)throw Error("Too many update redirects.");url=new URL(next,url).href;continue;}
 if(!r.ok){await r.body?.cancel();throw Error(r.status===404?"No published release is available.":r.status===403||r.status===429?"GitHub is limiting requests. Try again later.":"GitHub could not provide this update.");}return r;
 }throw Error("Too many update redirects.");
}
export async function githubJSON(url:string,signal:AbortSignal,fetcher?:typeof fetch){const r=await githubResponse(url,signal,fetcher);const chunks:Uint8Array[]=[];let bytes=0;try{if(!r.body)throw Error("Empty update metadata.");for await(const chunk of r.body){bytes+=chunk.byteLength;if(bytes>256*1024)throw Error("Update metadata is too large.");chunks.push(chunk);}return JSON.parse(Buffer.concat(chunks).toString("utf8"));}catch(e){await r.body?.cancel().catch(()=>{});throw e;}}
export async function downloadUpdate(url:string,file:string,expected:{size:number;sha256:string},signal:AbortSignal,progress:(received:number)=>void,fetcher?:typeof fetch){
 if(expected.size<=0||expected.size>MAX_PACKAGE)throw Error("Update package is too large.");const response=await githubResponse(url,signal,fetcher);let handle:Awaited<ReturnType<typeof open>>|undefined;let created=false;
 try{const header=response.headers.get("content-length");if(header!==null&&Number(header)!==expected.size)throw Error("Update package size does not match its release.");if(!response.body)throw Error("Update package is empty.");
 handle=await open(file,"wx",0o600);created=true;const hash=createHash("sha256");let received=0;for await(const chunk of response.body){signal.throwIfAborted();received+=chunk.byteLength;if(received>expected.size)throw Error("Update package exceeds its signed size.");hash.update(chunk);await handle.writeFile(chunk);progress(received);}
 signal.throwIfAborted();if(received!==expected.size||hash.digest("hex")!==expected.sha256)throw Error("Update package checksum could not be verified.");await handle.sync();await handle.close();handle=undefined;
 }catch(e){await handle?.close();await response.body?.cancel().catch(()=>{});if(created)await rm(file,{force:true});throw e;}
}
