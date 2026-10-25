import { cp, mkdir, readFile, writeFile, rm, lstat, access, chmod, rename } from "node:fs/promises";
import { join, resolve, dirname, isAbsolute, basename } from "node:path";
import { createRequire } from "node:module";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash, createPublicKey } from "node:crypto";
import { sealCommunityApp } from "./adhoc-sign.mjs";
import { bundleLinks, fileManifest, packageArguments, validateReleaseManifest } from "./package-utils.mjs";
const exec=promisify(execFile),root=import.meta.dirname,opts=packageArguments(process.argv.slice(2));
const platform=`${process.platform}-${process.arch}`;if(!["darwin-arm64","darwin-x64","linux-x64","win32-x64"].includes(platform))throw new Error(`Unsupported native package target: ${platform}`);
if(!isAbsolute(opts.out))throw new Error("Output path must be absolute.");const output=resolve(opts.out);
if(output===root||root.startsWith(output+"/"))throw new Error("Output cannot contain the source checkout.");
try{await access(output);throw new Error("Output already exists; select a new directory.");}catch(error){if(error.code!=="ENOENT")throw error;}
const pkg=JSON.parse(await readFile(join(root,"package.json"),"utf8")),dist=join(root,"dist");
if(opts.version)pkg.version=opts.version;
if(!opts.preview&&!opts["update-key"])opts["update-key"]=join(root,"resources/update-public.pem");
if(!opts.preview)await access(opts["update-key"]);
for(const f of ["main.cjs","preload.cjs","terminal-worker.cjs","update-worker.cjs","renderer/desktop.html","renderer/desktop-app.html","renderer/setup.html","renderer/settings.html","renderer/graph.html","renderer/memory.html","renderer/lifecycle.html"])await access(join(dist,f));
const native=join(dist,`node_modules/node-pty/prebuilds/${platform}`);await access(join(native,process.platform==="win32"?"conpty.node":"pty.node"));if(process.platform!=="win32")await access(join(native,"spawn-helper"));
const manifest=opts.manifest?validateReleaseManifest(JSON.parse(await readFile(opts.manifest,"utf8")),platform,{candidate:!opts.preview}):null;
if(!manifest&&!opts.preview)throw new Error("Candidate packaging requires --manifest. Use --preview only for an explicitly non-release local build.");
await fileManifest(dist); // source symlinks or unexpected specials cannot leak into a package
const require=createRequire(import.meta.url),electron=dirname(require.resolve("electron/package.json")),distribution=join(electron,"dist");
const name=opts.preview?"Crucible Preview":"Crucible",identity=opts.preview?"io.crucible.desktop.preview":"io.crucible.desktop";
await mkdir(output,{recursive:false});let complete=false;
try{
 const app=join(output,process.platform==="darwin"?`${name}.app`:name);
 await cp(process.platform==="darwin"?join(distribution,"Electron.app"):distribution,app,{recursive:true,verbatimSymlinks:true});
 const resources=process.platform==="darwin"?join(app,"Contents/Resources"):join(app,"resources");
 for(const name of ["LICENSE","LICENSES.chromium.html"])await cp(join(distribution,name),join(resources,name));
 await rm(join(resources,"default_app.asar"),{force:true});const payload=join(resources,"app");await mkdir(payload,{recursive:true});
 await writeFile(join(payload,"package.json"),JSON.stringify({name:"crucible-desktop",productName:name,version:pkg.version,main:"dist/main.cjs",private:true},null,2));
 const target=join(payload,"dist");await mkdir(target);for(const f of ["main.cjs","preload.cjs","terminal-worker.cjs","update-worker.cjs","renderer","resources"])await cp(join(dist,f),join(target,f),{recursive:true});
 const pty=join(target,"node_modules/node-pty");await mkdir(pty,{recursive:true});for(const f of ["lib","package.json","LICENSE",`prebuilds/${platform}`])await cp(join(dist,"node_modules/node-pty",f),join(pty,f),{recursive:true});
 if(process.platform!=="win32")await chmod(join(pty,`prebuilds/${platform}/spawn-helper`),0o755);
 if(opts["update-key"]){const keyText=await readFile(resolve(opts["update-key"]),"utf8");if(!keyText.includes("BEGIN PUBLIC KEY")||keyText.includes("PRIVATE KEY"))throw new Error("Provide only the public update verification key.");const key=createPublicKey(keyText);if(key.asymmetricKeyType!=="ed25519")throw new Error("Update verification requires an Ed25519 public key.");await writeFile(join(target,"resources/desktop-update-key.pem"),key.export({type:"spki",format:"pem"}));}
 if(manifest)await writeFile(join(target,"resources/runtime-manifest.json"),JSON.stringify(manifest,null,2));
 if(opts.artifacts){if(!manifest)throw new Error("Embedded artifacts require a pinned manifest.");const mappings={};const assets=join(target,"resources/runtime-assets");await mkdir(assets);for(const [id,s] of Object.entries(manifest.components)){if(id==="lsps")continue;const k=id==="agentd"||id==="jdtls"?"any":platform,url=s.urls[k],file=basename(new URL(url).pathname);if(!/^[a-zA-Z0-9_.+-]+$/.test(file))throw new Error("Invalid embedded artifact filename");const source=join(resolve(opts.artifacts),file);if(!(await lstat(source)).isFile()||(await lstat(source)).isSymbolicLink())throw new Error("Embedded artifact must be a regular file");const bytes=await readFile(source),sha=createHash("sha256").update(bytes).digest("hex");if(sha!==s.sha256[k].toLowerCase())throw new Error(`Embedded ${id} checksum mismatch`);await cp(source,join(assets,file));mappings[url]={file,sha256:sha};}await writeFile(join(target,"resources/runtime-assets.json"),JSON.stringify(mappings,null,2));}
 if(process.platform==="darwin"){
  const plists=[join(app,"Contents/Info.plist")];
  await exec("/usr/bin/plutil",["-remove","NSAppTransportSecurity.NSAllowsArbitraryLoads",plists[0]]);for(const helper of ["Electron Helper","Electron Helper (GPU)","Electron Helper (Renderer)","Electron Helper (Plugin)"])plists.push(join(app,`Contents/Frameworks/${helper}.app/Contents/Info.plist`));
  for(let i=0;i<plists.length;i++){await exec("/usr/bin/plutil",["-replace","CFBundleIdentifier","-string",i?`${identity}.helper${i}`:identity,plists[i]]);await exec("/usr/bin/plutil",["-replace","CFBundleName","-string",i?`${name} Helper`:name,plists[i]]);await exec("/usr/bin/plutil",["-replace","CFBundleDisplayName","-string",i?`${name} Helper`:name,plists[i]]);}
  await rename(join(app,"Contents/MacOS/Electron"),join(app,"Contents/MacOS",name));
  await exec("/usr/bin/plutil",["-replace","CFBundleExecutable","-string",name,plists[0]]);
  for(const suffix of [" Helper"," Helper (GPU)"," Helper (Renderer)"," Helper (Plugin)"]){const original=`Electron${suffix}`,branded=`${name}${suffix}`,directory=join(app,"Contents/Frameworks",`${original}.app`);await rename(join(directory,"Contents/MacOS",original),join(directory,"Contents/MacOS",branded));const plist=join(directory,"Contents/Info.plist");for(const key of ["CFBundleExecutable","CFBundleName","CFBundleDisplayName"])await exec("/usr/bin/plutil",["-replace",key,"-string",branded,plist]);await rename(directory,join(app,"Contents/Frameworks",`${branded}.app`));}
  const iconset=join(output,"crucible.iconset");await mkdir(iconset);
  for(const n of [16,32,128,256,512])for(const scale of [1,2])await exec("/usr/bin/sips",["-z",String(n*scale),String(n*scale),join(root,"../vscode-extension/resources/icon.png"),"--out",join(iconset,`icon_${n}x${n}${scale===2?"@2x":""}.png`)]);
  await exec("/usr/bin/iconutil",["-c","icns",iconset,"-o",join(resources,"crucible.icns")]);await rm(iconset,{recursive:true});
  await exec("/usr/bin/plutil",["-replace","CFBundleIconFile","-string","crucible.icns",plists[0]]);
  for(const key of ["CFBundleVersion","CFBundleShortVersionString"])await exec("/usr/bin/plutil",["-replace",key,"-string",pkg.version,plists[0]]);
 }else if(process.platform==="win32"){
  await rename(join(app,"electron.exe"),join(app,`${name}.exe`));
  try{await rename(join(app,"electron_wer.dll"),join(app,`${name}_wer.dll`));}catch(error){if(error.code!=="ENOENT")throw error;}
 }else if(process.platform==="linux"){
  await exec("mv",[join(app,"electron"),join(app,"crucible")]);await writeFile(join(app,"crucible.desktop"),`[Desktop Entry]\nName=${name}\nExec=crucible %F\nType=Application\nCategories=Development;\nTerminal=false\n`);
 }
 const executable=process.platform==="darwin"?`Contents/MacOS/${name}`:process.platform==="win32"?`${name}.exe`:"crucible";
 await access(join(app,executable));
 try{await access(join(app,process.platform==="darwin"?"Contents/MacOS/Electron":process.platform==="win32"?"electron.exe":"electron"));throw new Error("Default Electron executable must not remain in a packaged app.");}catch(error){if(error.code!=="ENOENT")throw error;}
 const codeSeal=process.platform==="darwin"?await sealCommunityApp(app):{kind:"none",appleDeveloperId:false,notarized:false,verified:false};
 const links=await bundleLinks(app);
 const files=await fileManifest(target);await writeFile(join(output,"package-evidence.json"),JSON.stringify({schemaVersion:1,platform,version:pkg.version,electron:JSON.parse(await readFile(join(electron,"package.json"))).version,identity,executable,preview:opts.preview,signed:false,codeSeal,releaseEligible:false,runtimeTag:manifest?.releaseTag??null,files,bundleLinks:links,distribution:"unsigned-community",remainingGates:["clean-machine-install-use-uninstall","native-pty","both-host-compatibility","accessibility-security-finder-drop","upgrade-preserves-stores","unsigned-install-update-rollback","platform-package-validation","release-key-authentication","macos-logout-wake","unsigned-keychain-notification-behavior","literal-dock-activation"]},null,2));
 const archive=join(output,`crucible-${pkg.version}-${platform}${opts.preview?"-preview":""}.${process.platform==="linux"?"tar.gz":"zip"}`);
 if(process.platform==="darwin")await exec("/usr/bin/ditto",["-c","-k","--norsrc","--noextattr","--keepParent",app,archive]);
 else if(process.platform==="linux")await exec("tar",["-czf",archive,"-C",output,name]);
 else await exec("powershell.exe",["-NoProfile","-NonInteractive","-Command","Compress-Archive -LiteralPath $env:CRUCIBLE_PACKAGE_SOURCE -DestinationPath $env:CRUCIBLE_PACKAGE_ARCHIVE"],{env:{...process.env,CRUCIBLE_PACKAGE_SOURCE:app,CRUCIBLE_PACKAGE_ARCHIVE:archive}});
 await writeFile(archive+".sha256",createHash("sha256").update(await readFile(archive)).digest("hex")+"  "+basename(archive)+"\n");complete=true;console.log(JSON.stringify({app,archive,evidence:join(output,"package-evidence.json"),signed:false,releaseEligible:false}));
}finally{if(!complete)await rm(output,{recursive:true,force:true});}
