import { readdir, lstat, readFile, readlink, realpath } from "node:fs/promises";
import { join, relative, isAbsolute } from "node:path";
import { createHash } from "node:crypto";
export function validateReleaseManifest(m,platform,{candidate=false}={}){
 if(candidate&&/local-test|\.invalid/i.test(JSON.stringify(m)))throw new Error("A distributable candidate cannot use local test runtime assets.");
 if(m?.manifestVersion!==1||typeof m.releaseTag!=="string"||!m.releaseTag||/dev|placeholder|unpinned/i.test(m.releaseTag))throw new Error("A pinned runtime manifest is required for a candidate.");
 for(const id of ["uv","agentd","indexer","ripgrep","rust-analyzer","gopls","jre","jdtls","lsps"]){const s=m.components?.[id];if(!s?.version)throw new Error(`Missing runtime component ${id}`);
  if(id==="lsps"){if(!s.npmPackages?.length||s.npmPackages.some(p=>!/^(@[a-z0-9-]+\/)?[a-z0-9-]+@\d+\.\d+\.\d+([+-][a-z0-9.-]+)?$/i.test(p)))throw new Error("Unpinned language server");continue;}
  const key=id==="agentd"||id==="jdtls"?"any":platform;const url=new URL(s.urls?.[key]??"invalid");if(url.protocol!=="https:"||url.username||url.password||url.hash||!/^[a-f0-9]{64}$/i.test(s.sha256?.[key]??""))throw new Error(`Unpinned runtime component ${id}`);
  if(id==="agentd"&&!url.pathname.endsWith(".whl"))throw new Error("Expected backend wheel");
 }return m;
}
export async function fileManifest(root){const files={};async function walk(dir){for(const name of (await readdir(dir)).sort()){const p=join(dir,name),s=await lstat(p);if(s.isSymbolicLink())throw new Error(`Unexpected source link: ${relative(root,p)}`);if(s.isDirectory())await walk(p);else if(s.isFile())files[relative(root,p).split("\\").join("/")]={bytes:s.size,sha256:createHash("sha256").update(await readFile(p)).digest("hex")};else throw new Error("Unsupported package source file");}}await walk(root);return files;}
export function packageArguments(args){const options={preview:false};for(let i=0;i<args.length;i++){const a=args[i];if(a==="--preview")options.preview=true;else if(["--out","--manifest","--artifacts","--update-key","--version"].includes(a)&&args[i+1]&&!args[i+1].startsWith("--"))options[a.slice(2)]=args[++i];else throw new Error(`Unknown or incomplete packaging option: ${a}`);}if(!options.out)throw new Error("Use --out with a new output directory.");if(options.version&&!/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(options.version))throw new Error("Use an exact three-part desktop version.");return options;}

/** Electron's trusted framework links must remain portable and inside this bundle. */
export async function bundleLinks(root){const canonical=await realpath(root),links={};async function walk(dir){for(const name of await readdir(dir)){const file=join(dir,name),info=await lstat(file);if(info.isSymbolicLink()){const target=await readlink(file),actual=await realpath(file),rel=relative(canonical,actual);if(isAbsolute(target)||isAbsolute(rel)||rel===".."||rel.startsWith("../")||rel.startsWith("..\\"))throw new Error("Electron bundle link escapes the packaged app: "+relative(root,file));links[relative(root,file).split("\\").join("/")]=target;}else if(info.isDirectory())await walk(file);}}await walk(root);return links;}
