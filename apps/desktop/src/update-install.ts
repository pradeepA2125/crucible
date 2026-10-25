import {access,cp,lstat,mkdtemp,readFile,realpath,rename,readdir,readlink,writeFile,rm,stat} from "node:fs/promises";
import {constants} from "node:fs";
import {join,dirname,basename,isAbsolute} from "node:path";
import {createHash} from "node:crypto";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {z} from "zod";
import {newer} from "./update-manifest.js";
import {validateBundleLinks} from "./update-archive.js";
const exec=promisify(execFile);
export async function verifyApplicationSeal(path:string){try{await exec("/usr/bin/codesign",["--verify","--deep","--strict","--verbose=2",path],{timeout:120000,maxBuffer:65536});}catch{throw Error("The downloaded app has an invalid code signature. Download a corrected release before installing.");}}
export async function quarantineDownload(path:string){await exec("/usr/bin/xattr",["-w","com.apple.quarantine",`0081;${Math.floor(Date.now()/1000).toString(16)};Crucible;`,path],{timeout:10000,maxBuffer:65536});}
export async function bundleIdentity(path:string){
 if(!isAbsolute(path)||(await realpath(path))!==path||!(await lstat(path)).isDirectory()||!path.endsWith('.app'))throw Error("Choose a regular installed application.");
 await validateBundleLinks(path);const plist=JSON.parse((await exec('/usr/bin/plutil',['-convert','json','-o','-',join(path,'Contents/Info.plist')],{maxBuffer:65536,timeout:10000})).stdout);
 const identity=z.object({CFBundleIdentifier:z.enum(['io.crucible.desktop','io.crucible.desktop.preview']),CFBundleShortVersionString:z.string().regex(/^\d+\.\d+\.\d+$/),CFBundleExecutable:z.enum(['Crucible','Crucible Preview'])}).parse(plist);
 const name=identity.CFBundleIdentifier.endsWith('.preview')?'Crucible Preview':'Crucible';if(identity.CFBundleExecutable!==name||basename(path)!==name+'.app')throw Error("Application identity does not match its executable.");
 const binary=join(path,'Contents/MacOS',name);if(!(await lstat(binary)).isFile())throw Error("Application executable is unavailable.");await access(binary,constants.X_OK);
 const pkg=JSON.parse(await readFile(join(path,'Contents/Resources/app/package.json'),'utf8'));if(pkg.version!==identity.CFBundleShortVersionString||pkg.main!=='dist/main.cjs')throw Error("Application package version is inconsistent.");
 const f=await import('node:fs/promises').then(fs=>fs.open(binary,'r'));const header=Buffer.alloc(4096);try{await f.read(header,0,header.length,0);}finally{await f.close();}
 const magic=header.readUInt32BE(0);let cpus:number[]=[];if(magic===0xcffaedfe)cpus=[header.readUInt32LE(4)];else if(magic===0xfeedfacf)cpus=[header.readUInt32BE(4)];else if(magic===0xcafebabe||magic===0xcafebabf){const count=header.readUInt32BE(4);if(count<1||count>16)throw Error('Invalid native application architecture.');const size=magic===0xcafebabe?20:32;cpus=Array.from({length:count},(_,i)=>header.readUInt32BE(8+i*size));}else throw Error('Expected a native macOS application executable.');
 const architectures=cpus.map(c=>c===0x100000c?'arm64':c===0x1000007?'x64':'unsupported');return {bundleId:identity.CFBundleIdentifier,version:identity.CFBundleShortVersionString,architectures};
}
/** Each newline-framed JSON record binds kind/path/mode and a fixed content hash, never raw file bytes. */
export async function treeDigest(root:string){const hash=createHash('sha256');const record=(value:unknown)=>hash.update(JSON.stringify(value)+'\n');const rootInfo=await lstat(root);if(!rootInfo.isDirectory()||rootInfo.isSymbolicLink())throw Error('Expected a regular application directory.');record(['.','directory',rootInfo.mode&0o777]);async function walk(dir:string):Promise<void>{for(const name of (await readdir(dir)).sort()){const path=join(dir,name),s=await lstat(path),rel=path.slice(root.length+1),mode=s.mode&0o777;if(s.isSymbolicLink())record([rel,'link',mode,await readlink(path)]);else if(s.isDirectory()){record([rel,'directory',mode]);await walk(path);}else if(s.isFile()){const content=createHash('sha256'),stream=await import('node:fs').then(fs=>fs.createReadStream(path));for await(const bytes of stream)content.update(bytes);record([rel,'file',mode,content.digest('hex')]);}else throw Error('Unexpected application entry.');}}await walk(root);return hash.digest('hex');}

const fingerprint=(s:Awaited<ReturnType<typeof stat>>)=>({dev:s.dev,ino:s.ino});
export const installPlan=z.object({version:z.literal(1),parentPid:z.number().int().positive(),current:z.string(),candidate:z.string(),backup:z.string(),work:z.string(),result:z.string(),profile:z.string(),old:z.object({bundleId:z.string(),version:z.string()}),next:z.object({bundleId:z.string(),version:z.string()}),currentIdentity:z.object({dev:z.number(),ino:z.number()}),candidateDigest:z.string().regex(/^[a-f0-9]{64}$/),currentDigest:z.string().regex(/^[a-f0-9]{64}$/)}).strict();
export type InstallPlan=z.infer<typeof installPlan>;
export async function waitForParentExit(pid:number,deadline:number){while(true){let alive=true;try{process.kill(pid,0);}catch(e){if((e as NodeJS.ErrnoException).code==='ESRCH')alive=false;else throw e;}if(!alive)return;if(Date.now()>=deadline)throw Error('Original application is still running. Update was not installed.');await new Promise(r=>setTimeout(r,200));}}
export async function prepareInstall(current:string,candidate:string,profile:string,parentPid:number):Promise<InstallPlan>{
 const old=await bundleIdentity(current),next=await bundleIdentity(candidate);await verifyApplicationSeal(candidate);if(old.bundleId!==next.bundleId||!newer(next.version,old.version))throw Error('Updates must keep the same application identity.');
 const parent=dirname(current);if(await realpath(parent)!==parent)throw Error('Symbolic installation folders are unsupported.');await access(parent,constants.W_OK);
 let retained=0;for(const name of await readdir(parent)){if(!/^\.crucible-update-[a-zA-Z0-9]{6}$/.test(name))continue;try{const record=installPlan.parse(JSON.parse(await readFile(join(parent,name,'request.json'),'utf8')));if(record.current===current&&record.profile===profile)retained++;}catch{}}if(retained>=2)throw Error('Two earlier update backups are retained. Clear older app backups or install the downloaded app manually.');
 const work=await mkdtemp(join(parent,'.crucible-update-'));const staged=join(work,basename(current));try{await cp(candidate,staged,{recursive:true,verbatimSymlinks:true,errorOnExist:true,force:false});
 await bundleIdentity(staged);await verifyApplicationSeal(staged);await quarantineDownload(staged);return installPlan.parse({version:1,parentPid,current,candidate:staged,backup:join(work,'previous',basename(current)),work,result:join(profile,'updates','last-install.json'),profile,old,next,currentIdentity:fingerprint(await stat(current)),candidateDigest:await treeDigest(staged),currentDigest:await treeDigest(current)});
 }catch(e){await rm(work,{recursive:true,force:true});throw e;}
}
export async function performSwap(raw:unknown,launch:(app:string,profile:string)=>Promise<void>,move:typeof rename=rename){
 const p=installPlan.parse(raw);const parent=dirname(p.current);if(!isAbsolute(parent)||await realpath(p.work)!==p.work||dirname(p.work)!==parent||!basename(p.work).startsWith('.crucible-update-')||p.candidate!==join(p.work,basename(p.current))||p.backup!==join(p.work,'previous',basename(p.current))||p.result!==join(p.profile,'updates','last-install.json'))throw Error('Invalid update transaction.');
 const current=await bundleIdentity(p.current),next=await bundleIdentity(p.candidate),now=fingerprint(await stat(p.current));if(JSON.stringify(now)!==JSON.stringify(p.currentIdentity)||current.bundleId!==p.old.bundleId||current.version!==p.old.version||next.bundleId!==p.next.bundleId||next.version!==p.next.version||next.bundleId!==current.bundleId||!newer(next.version,current.version)||await treeDigest(p.current)!==p.currentDigest||await treeDigest(p.candidate)!==p.candidateDigest)throw Error('Application changed after update preparation.');
 await verifyApplicationSeal(p.candidate);
 const quarantine=(await exec('/usr/bin/xattr',['-p','com.apple.quarantine',p.candidate],{timeout:10000,maxBuffer:65536})).stdout.trim();if(!/^0081;[a-f0-9]+;Crucible;$/.test(quarantine))throw Error('Downloaded application approval marker changed.');
 await import('node:fs/promises').then(fs=>fs.mkdir(dirname(p.backup),{mode:0o700}));let moved=false;
 try{await move(p.current,p.backup);moved=true;await move(p.candidate,p.current);}catch(e){if(moved){try{await lstat(p.current);}catch(missing){if((missing as NodeJS.ErrnoException).code==='ENOENT')await move(p.backup,p.current);}}throw e;}
 const record={version:1,app:p.current,backup:p.backup,oldVersion:p.old.version,newVersion:p.next.version,backupDigest:p.currentDigest,status:'launch-requested' as string,startupVerified:false};
 // Once launch has been requested, never replace a possibly running bundle.
 try{await launch(p.current,p.profile);}catch{record.status='installed-launch-pending';}
 await writeFile(p.result,JSON.stringify(record),{mode:0o600});return record;
}
