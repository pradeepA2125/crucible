import { sign } from "@electron/osx-sign";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const exec=promisify(execFile);
/** Free content sealing only. No Apple identity lookup, provisioning profile, timestamp service or notarization. */
export function adhocOptions(app){return {app,platform:"darwin",identity:"-",identityValidation:false,preAutoEntitlements:false,preEmbedProvisioningProfile:false,strictVerify:true,
 ignore:file=>file.includes("/dist/resources/runtime-assets/"),
 optionsForFile:()=>({hardenedRuntime:true,timestamp:"none",entitlements:["com.apple.security.cs.allow-jit","com.apple.security.cs.disable-library-validation"]})};}
export async function sealCommunityApp(app){await sign(adhocOptions(app));await exec("/usr/bin/codesign",["--verify","--deep","--strict","--verbose=2",app],{timeout:120000,maxBuffer:1024*1024});const result=await exec("/usr/bin/codesign",["--display","--verbose=4",app],{timeout:10000,maxBuffer:65536});const text=result.stdout+result.stderr;if(!text.includes("Signature=adhoc")||!text.includes("TeamIdentifier=not set"))throw Error("Expected free ad-hoc sealing without an Apple signing identity.");return {kind:"ad-hoc",appleDeveloperId:false,notarized:false,verified:true};}
