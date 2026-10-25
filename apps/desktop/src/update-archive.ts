import * as yauzl from "yauzl";
import {mkdir,open,symlink,chmod,readlink,readdir,lstat,realpath} from "node:fs/promises";
import {join,posix,relative,isAbsolute} from "node:path";
import {pipeline} from "node:stream/promises";
import {Transform} from "node:stream";
const MAX_EXPANDED=2*1024*1024*1024,MAX_ENTRIES=50000;
export async function extractUpdate(archive:string,destination:string,signal:AbortSignal){
 const zip=await new Promise<yauzl.ZipFile>((resolve,reject)=>yauzl.open(archive,{lazyEntries:true,autoClose:false,strictFileNames:true,validateEntrySizes:true},(e,z)=>e?reject(e):resolve(z!)));
 const entries:yauzl.Entry[]=[];const names=new Set<string>(),links=new Map<string,string>();let total=0,root="";
 try{
 await new Promise<void>((resolve,reject)=>{zip.on("error",reject);zip.on("end",resolve);zip.on("entry",(entry:yauzl.Entry)=>{try{signal.throwIfAborted();const name=entry.fileName,clean=name.replace(/\/$/,""),parts=clean.split('/');
 if(!clean||/[\\\x00-\x1f\x7f:]/.test(name)||parts.some(p=>!p||p==='.'||p==='..')||isAbsolute(name)||posix.normalize(clean)!==clean)throw Error("Unsafe path in update archive.");
 // Resource-fork duplicates are unnecessary for our explicit application payload.
 if(parts[0]==="__MACOSX")throw Error("Unexpected archive metadata. Rebuild without resource-fork entries.");
 if(!root){root=parts[0]!;if(!/^(Crucible|Crucible Preview)\.app$/.test(root))throw Error("Expected one Crucible application.");}if(parts[0]!==root)throw Error("Archive contains more than one application.");
 const key=clean.normalize("NFC").toLowerCase();if(names.has(key))throw Error("Duplicate or case-aliased update path.");names.add(key);const kind=(entry.externalFileAttributes>>>16)&0o170000;
 if(![0,0o100000,0o040000,0o120000].includes(kind)||!!(entry.generalPurposeBitFlag&1)||entry.uncompressedSize<0)throw Error("Unsupported update archive entry.");
 total+=entry.uncompressedSize;if(total>MAX_EXPANDED||entries.length>=MAX_ENTRIES)throw Error("Update archive expansion exceeds its limit.");if(kind===0o120000){if(entry.uncompressedSize>4096)throw Error("Oversized archive link.");links.set(clean,"");}entries.push(entry);zip.readEntry();
 }catch(e){reject(e);}});zip.readEntry();});
 const stream=(e:yauzl.Entry)=>new Promise<NodeJS.ReadableStream>((resolve,reject)=>zip.openReadStream(e,(error,s)=>error?reject(error):resolve(s!)));
 for(const entry of entries){signal.throwIfAborted();const name=entry.fileName.replace(/\/$/,"");for(let p=posix.dirname(name);p!=='.';p=posix.dirname(p))if(links.has(p))throw Error("Archive writes through a symbolic link.");
 if(links.has(name)){const chunks:Buffer[]=[];let count=0;for await(const c of await stream(entry) as AsyncIterable<Buffer>){count+=c.length;if(count>4096)throw Error("Oversized archive link.");chunks.push(c);}const target=Buffer.concat(chunks).toString('utf8');const actual=posix.normalize(posix.join(posix.dirname(name),target));if(!target||/[\\\x00-\x1f\x7f]/.test(target)||posix.isAbsolute(target)||!actual.startsWith(root+'/'))throw Error("Archive link escapes its application.");links.set(name,target);continue;}
 const path=join(destination,name);if(entry.fileName.endsWith('/')){await mkdir(path,{recursive:true,mode:0o755});continue;}
 await mkdir(posix.dirname(path),{recursive:true,mode:0o755});const file=await open(path,"wx",0o600);let size=0;try{await pipeline(await stream(entry),new Transform({transform(chunk,_encoding,done){size+=chunk.length;if(size>entry.uncompressedSize)done(Error("Archive entry exceeds its declared size."));else done(null,chunk);}}),file.createWriteStream(),{signal});}finally{await file.close();}await chmod(path,((entry.externalFileAttributes>>>16)&0o111)?0o755:0o644);
 }
 for(const [name,target] of links){await mkdir(posix.dirname(join(destination,name)),{recursive:true,mode:0o755});await symlink(target,join(destination,name));}
 const app=join(destination,root);await validateBundleLinks(app);return app;
 }finally{zip.close();}
}
export async function validateBundleLinks(root:string){const base=await realpath(root);async function walk(dir:string):Promise<void>{for(const name of await readdir(dir)){const path=join(dir,name),s=await lstat(path);if(s.isSymbolicLink()){const link=await readlink(path),actual=await realpath(path),r=relative(base,actual);if(isAbsolute(link)||r==='..'||r.startsWith('../')||isAbsolute(r))throw Error("Bundle link is outside the application.");}else if(s.isDirectory())await walk(path);else if(!s.isFile())throw Error("Unexpected bundle entry.");}}await walk(base);}
