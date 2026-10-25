import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
export const REQUIRED=["clean-machine-install-use-uninstall","native-pty","both-host-compatibility","accessibility-security-finder-drop","upgrade-preserves-stores","signed-update-rollback","platform-signing-notarization","macos-logout-wake","signed-notification-click","literal-dock-activation"];
export const UNSIGNED_REQUIRED=[...REQUIRED.filter(id=>!["signed-update-rollback","platform-signing-notarization","signed-notification-click"].includes(id)),"platform-package-validation","unsigned-install-update-rollback","release-key-authentication","unsigned-keychain-notification-behavior"];
export function releaseGates(packet){
 if(!["signed","unsigned-community"].includes(packet?.distribution)||packet.signed!==(packet.distribution==="signed")||packet.notarized!==(packet.distribution==="signed"))return {ok:false,missing:["explicit truthful distribution profile"]};
 if(packet?.schemaVersion!==1||typeof packet.artifactSha256!=="string"||!/^[a-f0-9]{64}$/.test(packet.artifactSha256)||!Array.isArray(packet.evidence))return {ok:false,missing:["valid artifact-bound evidence packet"]};
 const required=packet.distribution==="unsigned-community"?UNSIGNED_REQUIRED:REQUIRED;
 const missing=required.filter(id=>!packet.evidence.some(e=>e.id===id&&e.status==="passed"&&e.artifactSha256===packet.artifactSha256&&typeof e.reviewer==="string"&&e.reviewer.trim()&&typeof e.evidence==="string"&&e.evidence.trim()));
 return {ok:missing.length===0,missing};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){const path=process.argv[2];if(!path)throw new Error("Pass the reviewed artifact-bound release evidence JSON.");const result=releaseGates(JSON.parse(await readFile(path,"utf8")));console.log(JSON.stringify(result,null,2));if(!result.ok)process.exitCode=1;}
