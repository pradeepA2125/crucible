import {mkdir,mkdtemp,readFile,rm,writeFile,realpath,access,readdir,lstat} from "node:fs/promises";
import {constants} from "node:fs";
import {join,dirname} from "node:path";
import {createHash} from "node:crypto";
import {spawn} from "node:child_process";
import {REPOSITORY,RELEASES,githubRelease,verifiedManifest,releaseAsset,manifestAsset,type UpdateManifest} from "./update-manifest.js";
import {githubJSON,downloadUpdate} from "./update-download.js";
import {extractUpdate} from "./update-archive.js";
import {bundleIdentity,prepareInstall,installPlan,treeDigest,verifyApplicationSeal} from "./update-install.js";
import type {DesktopUpdateState} from "./update-protocol.js";
export interface UpdateOptions {profile:string;resources:string;currentApp?:string|undefined;currentVersion:string;target:string;bundleId:string;publish:(state:DesktopUpdateState)=>void;fetcher?:typeof fetch;}
export class DesktopUpdates {
 private disposed=false;private initialized:Promise<void>;
 private completion:Promise<void>|undefined;private complete:(()=>void)|undefined;
 private state:DesktopUpdateState;private flight:AbortController|undefined;private candidate?:UpdateManifest;private staged:string|undefined;private stagedDigest:string|undefined;private cache?:string;private backup?:string;private backupDigest?:string;
 constructor(private options:UpdateOptions){this.state={phase:'idle',currentVersion:options.currentVersion,message:'Check GitHub for a newer version of Crucible.',canDownload:false,canInstall:false,backupAvailable:false};this.initialized=this.restore();}
 read(){this.options.publish({...this.state});return {...this.state};}
 private set(patch:Partial<DesktopUpdateState>){if(patch.message)patch={...patch,message:patch.message.slice(0,1000)};this.state={...this.state,...patch};this.read();}
 private started(){this.completion=new Promise<void>(resolve=>{this.complete=resolve;});}
 private finished(){this.flight=undefined;this.complete?.();this.complete=undefined;this.completion=undefined;}
 private async key(){try{return await readFile(join(this.options.resources,'desktop-update-key.pem'),'utf8');}catch{throw Error('This build cannot verify downloaded updates yet. Download a build from the project’s GitHub releases.');}}
 private async restore(){
 const cacheRoot=join(this.options.profile,'updates');try{if(await realpath(cacheRoot)===cacheRoot){for(const name of await readdir(cacheRoot)){if(!/^download-[a-zA-Z0-9]{6}$/.test(name))continue;const path=join(cacheRoot,name);if(this.options.currentApp&&(this.options.currentApp===path||this.options.currentApp.startsWith(path+'/')))continue;if(!(await lstat(path)).isDirectory())continue;try{if(await readFile(join(path,'.crucible-download'),'utf8')==='1')await rm(path,{recursive:true,force:true});}catch{}}}}catch{}
 try{const r=JSON.parse(await readFile(join(cacheRoot,'last-install.json'),'utf8'));if(r?.version===1&&this.options.currentApp&&r.app===this.options.currentApp){if(r.status==='failed'){this.set({phase:'error',message:'The update could not be installed. Your previous app is retained. Check for updates to retry.'});}if(typeof r.backup==='string'){const b=await bundleIdentity(r.backup);if(b.bundleId===this.options.bundleId&&typeof r.backupDigest==='string'&&/^[a-f0-9]{64}$/.test(r.backupDigest)&&await treeDigest(r.backup)===r.backupDigest){this.backup=r.backup;this.backupDigest=r.backupDigest;this.set({backupAvailable:true,message:r.status==='launch-requested'?'The app was replaced and macOS was asked to open it. The previous version is retained.':'Update installation needs attention. The previous version is retained for recovery.'});}}}}catch{}
 }

 async check(){await this.initialized;if(this.disposed)throw Error("Updates are closing.");if(this.flight)throw Error('An update action is already running.');const controller=new AbortController();this.flight=controller;this.started();const timeout=setTimeout(()=>controller.abort(),30000);this.set({phase:'checking',message:'Checking GitHub releases…',canDownload:false,canInstall:false});
 try{const release=githubRelease(await githubJSON(`https://api.github.com/repos/${REPOSITORY}/releases/latest`,controller.signal,this.options.fetcher),this.options.target);
 if(!release.manifest){this.set({phase:this.staged?'ready':'idle',message:'No compatible standalone update is published in the latest release.',canInstall:!!this.staged&&await this.installable()});return;}
 const key=await this.key(),wire=await githubJSON(releaseAsset(release.tag,manifestAsset(this.options.target)),controller.signal,this.options.fetcher);const manifest=verifiedManifest(wire,key,{tag:release.tag,target:this.options.target,bundleId:this.options.bundleId,currentVersion:this.options.currentVersion});
 const asset=release.assets.filter(a=>a.name===manifest.asset);if(asset.length!==1||asset[0]!.size!==manifest.size)throw Error('The release package does not match its verified manifest.');
 this.candidate=manifest;this.set({phase:'available',version:manifest.version,notes:release.notes,message:'A new version is available.',canDownload:true,canInstall:false,received:0,total:manifest.size});
 }catch(e){if(e instanceof Error&&e.message==='UP_TO_DATE'){this.set({phase:'idle',message:'Crucible is up to date.',canDownload:false,canInstall:false});}else this.set({phase:'error',message:controller.signal.aborted?'Update check cancelled.':e instanceof Error?e.message:'Update check failed.',canDownload:false,canInstall:false});}
 finally{clearTimeout(timeout);this.finished();}}
 async download(){await this.initialized;if(this.disposed)throw Error("Updates are closing.");if(this.flight)throw Error('An update action is already running.');const candidate=this.candidate;if(!candidate||!this.state.canDownload)throw Error('Check for a verified update first.');
 const controller=new AbortController();this.flight=controller;this.started();const timeout=setTimeout(()=>controller.abort(),30*60*1000);let cache:string|undefined;this.set({phase:'downloading',message:'Downloading and verifying the update…',canDownload:false,canInstall:false,received:0});
 try{const root=join(this.options.profile,'updates');await mkdir(root,{recursive:true,mode:0o700});if(await realpath(root)!==root)throw Error('The update cache folder changed.');cache=await mkdtemp(join(root,'download-'));await writeFile(join(cache,'.crucible-download'),'1',{mode:0o600});const archive=join(cache,'application.zip');let last=0;
 await downloadUpdate(releaseAsset(candidate.tag,candidate.asset),archive,candidate,controller.signal,received=>{if(Date.now()-last>100){last=Date.now();this.set({received});}},this.options.fetcher);
 const extracted=join(cache,'extracted');await mkdir(extracted,{mode:0o700});const app=await extractUpdate(archive,extracted,controller.signal);const identity=await bundleIdentity(app);await verifyApplicationSeal(app);if(identity.bundleId!==candidate.bundleId||identity.version!==candidate.version||!identity.architectures.some(arch=>arch===candidate.target.split('-')[1]))throw Error('Downloaded application identity does not match its release.');controller.signal.throwIfAborted();const digest=await treeDigest(app);controller.signal.throwIfAborted();
 if(this.cache)await rm(this.cache,{recursive:true,force:true});this.cache=cache;cache=undefined;this.staged=app;this.stagedDigest=digest;this.set({phase:'ready',message:'Update verified and ready.',received:candidate.size,canInstall:await this.installable(),canDownload:false});
 }catch(e){if(cache)await rm(cache,{recursive:true,force:true});this.set({phase:'error',message:controller.signal.aborted?'Download cancelled.':e instanceof Error?e.message:'Download failed.',canDownload:true,canInstall:false});}
 finally{clearTimeout(timeout);this.finished();}}
 cancel(){this.flight?.abort();}
 async dispose(){this.disposed=true;this.cancel();await this.initialized;await this.completion;}
 private changedDownload(){this.staged=undefined;this.stagedDigest=undefined;this.set({phase:'error',message:'The downloaded app changed after verification. Download the update again.',canDownload:!!this.candidate,canInstall:false});return Error('The downloaded app changed after verification. Download the update again.');}
 private async verifiedStage(){if(!this.staged||!this.stagedDigest)throw Error('No verified update is ready.');try{if(await realpath(this.staged)!==this.staged||await treeDigest(this.staged)!==this.stagedDigest)throw Error('Changed download.');return {app:this.staged,digest:this.stagedDigest};}catch{throw this.changedDownload();}}
 async reveal(){if(this.state.phase!=='ready')throw Error('No verified update is ready.');return (await this.verifiedStage()).app;}
 async previous(){if(!this.backup||!this.backupDigest)throw Error('No verified previous version is available.');if(await realpath(this.backup)!==this.backup||await treeDigest(this.backup)!==this.backupDigest){this.set({backupAvailable:false});throw Error('The previous app changed. Download a verified version from GitHub instead.');}return this.backup;}
 releases(){return this.candidate?`${RELEASES}/tag/${this.candidate.tag}`:RELEASES;}
 private async installable(){if(!this.options.currentApp||!this.options.target.startsWith('darwin-')||this.options.currentApp.startsWith(join(this.options.profile,'updates')+'/')||this.options.currentApp.split('/').some(p=>p.startsWith('.crucible-update-')))return false;try{if(await realpath(dirname(this.options.currentApp))!==dirname(this.options.currentApp))return false;await access(dirname(this.options.currentApp),constants.W_OK);return true;}catch{return false;}}
 async prepare(){if(this.disposed||this.flight||this.state.phase!=='ready'||!this.staged||!this.candidate||!this.state.canInstall||!this.options.currentApp)throw Error('A verified update must be ready before installation.');
 const verified=await this.verifiedStage();
 const plan=await prepareInstall(this.options.currentApp,verified.app,this.options.profile,process.pid).catch(async error=>{await this.verifiedStage();throw error;});
 let previous:string|null=null;try{try{previous=await readFile(plan.result,'utf8');}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}if(plan.candidateDigest!==verified.digest)throw this.changedDownload();if(plan.next.version!==this.candidate.version)throw Error('Prepared update changed.');const request=join(plan.work,'request.json'),bytes=JSON.stringify(installPlan.parse(plan));await writeFile(request,bytes,{mode:0o600});this.set({phase:'installing',message:'Saving work and preparing to restart…',canInstall:false});
 let launched=false,wrotePending=false;return {launch:async()=>{if(launched)return;await writeFile(plan.result,JSON.stringify({version:1,status:'pending',app:plan.current,backup:plan.backup,oldVersion:plan.old.version,newVersion:plan.next.version}),{mode:0o600});wrotePending=true;const child=spawn(process.execPath,[join(__dirname,'update-worker.cjs'),request,createHash('sha256').update(bytes).digest('hex')],{detached:true,stdio:'ignore',env:{PATH:'/usr/bin:/bin:/usr/sbin:/sbin',ELECTRON_RUN_AS_NODE:'1'}});await new Promise<void>((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject);});child.unref();launched=true;},cancel:async()=>{if(!launched){await rm(plan.work,{recursive:true,force:true});if(wrotePending){if(previous===null)await rm(plan.result,{force:true});else await writeFile(plan.result,previous,{mode:0o600});}}this.set({phase:'ready',message:'Installation cancelled. Your verified download is retained.',canInstall:await this.installable()});}};
 }catch(e){await rm(plan.work,{recursive:true,force:true});throw e;}

 }
 error(message:string){this.set({message:message.slice(0,1000)});}
}
