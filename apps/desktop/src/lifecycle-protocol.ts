import { z } from "zod";
const id = z.string().min(1).max(1024);
const label = z.string().max(200);
const resource = z.object({ id, label }).strict();
export const lifecycleThread = z.object({
  thread_id: id, title: label, turn_active: z.boolean(), gates: z.number().int().min(0).max(256), gate_ids:z.array(id).max(256),
  agents_running: z.number().int().min(0).max(1024), sessions_running: z.number().int().min(0).max(1024),
  agents: z.array(resource).max(1024), teams: z.array(resource).max(1024), sessions: z.array(resource).max(1024),
}).strict().refine(t => t.gates===t.gate_ids.length && new Set(t.gate_ids).size===t.gate_ids.length && t.agents_running === t.agents.length && t.sessions_running === t.sessions.length, "Incomplete resource metadata");
export const lifecycleReply = z.discriminatedUnion("supported", [
  z.object({version:z.literal(1),request_id:z.string().uuid(),requested_workspace:id,supported:z.literal(false),reason:id}).strict(),
  z.object({version:z.literal(1),request_id:z.string().uuid(),requested_workspace:id,supported:z.literal(true),process_id:id,workspace_path:id,
    threads:z.array(lifecycleThread).max(1000)}).strict(),
]);
export type LifecycleThread = z.infer<typeof lifecycleThread>;
export interface LifecycleEvidence { identity: string; owner: boolean; threads: LifecycleThread[]; }
export const lifecyclePreferences = z.object({notifications:z.enum(["off","attention","all"]),keepAwake:z.boolean()}).strict();
export type LifecyclePreferences = z.infer<typeof lifecyclePreferences>;
export const lifecycleChoice = z.enum(["cancel","background","quit","discard"]);
export interface QuitProject { id:string; name:string; state:"idle"|"busy"|"unknown"; owner:boolean; threads:LifecycleThread[]; shells:Array<{id:string;label:string}>; }
export interface QuitPreview { id:string; projects:QuitProject[]; error?:string; phase:"preview"|"draft-error"|"cleanup-error"; }
export const lifecycleCommands = {
  "desktop/lifecycleRead": {},
  "desktop/lifecycleSet": { preferences:lifecyclePreferences },
  "desktop/lifecycleTest": {},
  "desktop/lifecycleReady": {},
  "desktop/lifecycleChoice": { snapshotId:z.string().uuid(),choice:lifecycleChoice },
};

export async function readLifecycleReply(response:Response):Promise<z.infer<typeof lifecycleReply>> {
  if(!response.body)throw new Error("Lifecycle response has no body.");
  const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0;
  try{for(;;){const item=await reader.read();if(item.done)break;size+=item.value.byteLength;
    if(size>2*1024*1024)throw new Error("Lifecycle metadata exceeds its limit.");chunks.push(item.value);}
    return lifecycleReply.parse(JSON.parse(Buffer.concat(chunks).toString("utf8")));
  }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
