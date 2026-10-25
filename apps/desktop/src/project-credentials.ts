import { readFileSync, writeFileSync, mkdirSync, renameSync, unlinkSync, existsSync } from "node:fs";
import { dirname } from "node:path";
import { randomUUID, createHash } from "node:crypto";
import { PROVIDERS } from "../../vscode-extension/src/setup-data.js";
import { PROVIDER_KEY_ENV } from "../../vscode-extension/src/provider-credentials.js";
import type { BackendSettings } from "../../vscode-extension/src/runtime/backend-process.js";
const extra = new Set(PROVIDERS.flatMap(p=>p.extraFields?.map(f=>f.envVar)??[]));
const credentials = new Set([...Object.values(PROVIDER_KEY_ENV),...extra,"CRUCIBLE_CHATGPT_REGISTRATION","CRUCIBLE_CLAUDE_REGISTRATION"]);
export function projectSpawnEnv(env: Record<string,string>, settings?: BackendSettings): Record<string,string> {
  const next={...env}; for(const key of credentials)delete next[key];
  // Watchers receive no project credentials. Backend receives only this project's captured configuration.
  if(settings){if(settings.apiKey)next[settings.apiKey.envVar]=settings.apiKey.value;
    for(const [key,value] of Object.entries(settings.extraEnv??{}))if(credentials.has(key))next[key]=value;}
  return next;
}
/** Explicit candidate-only import copies ciphertext without exposing plaintext or deleting the legacy source. */
export function importLegacyCredentials(source: string, target: string): string {
  const bytes=readFileSync(source); if(bytes.length>2*1024*1024)throw new Error("Previous credential file exceeds the size limit.");
  const raw:unknown=JSON.parse(bytes.toString("utf8"));if(!raw || typeof raw!=="object" || Array.isArray(raw))throw new Error("Previous credential file is invalid.");
  const allowed=new Set(PROVIDERS.flatMap(p=>[`crucible.providerKey.${p.id}`,...(p.extraFields??[]).map(f=>`crucible.providerExtra.${p.id}.${f.envVar}`)]));
  const values:Record<string,string>={};
  for(const [key,value] of Object.entries(raw))if(allowed.has(key)){
    if(typeof value!=="string" || value.length>128*1024 || !/^[A-Za-z0-9+/]*={0,2}$/.test(value))throw new Error("Previous encrypted credential is invalid.");values[key]=value;}
  if(!Object.keys(values).length)throw new Error("No previous encrypted provider keys were found.");
  if(existsSync(target))throw new Error("This project already has credential storage. Existing keys were kept; use Provider settings to enter a key.");
  mkdirSync(dirname(target),{recursive:true,mode:0o700});const temp=`${target}.${randomUUID()}.tmp`;
  try{writeFileSync(temp,JSON.stringify(values),{mode:0o600,flag:"wx"});renameSync(temp,target);}finally{try{unlinkSync(temp);}catch{}}
  return createHash("sha256").update(bytes).digest("hex");
}
