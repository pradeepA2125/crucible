import {createPublicKey,verify} from "node:crypto";
import {z} from "zod";
export const REPOSITORY="pradeepA2125/crucible";
export const RELEASES=`https://github.com/${REPOSITORY}/releases`;
export const MAX_PACKAGE=1024*1024*1024;
const version=z.string().regex(/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/);
export const updateManifest=z.object({schemaVersion:z.literal(1),tag:z.string().regex(/^v?\d+\.\d+\.\d+$/),version,
 target:z.enum(["darwin-arm64","darwin-x64"]),bundleId:z.enum(["io.crucible.desktop","io.crucible.desktop.preview"]),
 asset:z.string().regex(/^crucible-[a-zA-Z0-9.+_-]+\.zip$/).max(200),size:z.number().int().positive().max(MAX_PACKAGE),sha256:z.string().regex(/^[a-f0-9]{64}$/)}).strict();
export type UpdateManifest=z.infer<typeof updateManifest>;
export function newer(candidate:string,current:string):boolean {const a=version.parse(candidate).split('.').map(Number),b=version.parse(current).split('.').map(Number);for(let i=0;i<3;i++){if(a[i]!==b[i])return a[i]!>b[i]!;}return false;}
export const manifestAsset=(target:string)=>`crucible-desktop-update-${target}.json`;
export function releaseAsset(tag:string,name:string){if(!/^v?\d+\.\d+\.\d+$/.test(tag)||!/^[a-zA-Z0-9._+-]+$/.test(name))throw Error("Invalid release asset.");return `${RELEASES}/download/${tag}/${name}`;}
export function verifiedManifest(wire:unknown,key:string,expected:{tag:string;target:string;bundleId:string;currentVersion:string}):UpdateManifest {
 const signed=z.object({payload:z.string().max(65536),signature:z.string().max(256)}).strict().parse(wire);
 const publicKey=createPublicKey(key);if(publicKey.asymmetricKeyType!=="ed25519")throw Error("Invalid update verification key.");
 const bytes=Buffer.from(signed.payload,"base64"),signature=Buffer.from(signed.signature,"base64");
 if(bytes.toString("base64")!==signed.payload||signature.length!==64||signature.toString("base64")!==signed.signature||!verify(null,bytes,publicKey,signature))throw Error("Update signature could not be verified.");
 const result=updateManifest.parse(JSON.parse(bytes.toString("utf8")));
 if(result.tag!==expected.tag||result.tag.replace(/^v/,"")!==result.version||result.target!==expected.target||result.bundleId!==expected.bundleId)throw Error("This update does not match this application or is not newer.");
 if(!newer(result.version,expected.currentVersion))throw Error("UP_TO_DATE");
 return result;
}
export function githubRelease(value:unknown,target:string){
 const r=z.object({tag_name:z.string().max(64),draft:z.literal(false),prerelease:z.literal(false),body:z.string().max(200000).nullable().optional(),assets:z.array(z.object({name:z.string().max(200),size:z.number().int().nonnegative().max(MAX_PACKAGE),browser_download_url:z.string().max(4096)})).max(1000)}).parse(value);
 if(!/^v?\d+\.\d+\.\d+$/.test(r.tag_name))throw Error("Latest release does not have a stable version.");
 for(const asset of r.assets)if(asset.browser_download_url!==releaseAsset(r.tag_name,asset.name))throw Error("Release asset has an unexpected download address.");
 const selected=r.assets.filter(a=>a.name===manifestAsset(target));if(selected.length>1)throw Error("Ambiguous desktop update manifest.");return {tag:r.tag_name,manifest:selected[0],assets:r.assets,notes:(r.body??'').slice(0,12000)};
}
