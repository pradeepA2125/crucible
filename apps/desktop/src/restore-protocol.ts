import { z } from "zod";
import { baselineIdentity, baselinePath } from "./baseline-protocol.js";
const id=z.string().uuid();
export const restoreAction=z.discriminatedUnion("operation",[
  z.object({operation:z.literal("preview"),path:baselinePath,checkpoint:baselineIdentity,supplying:baselineIdentity,fingerprint:z.string().regex(/^[a-f0-9]{64}$/)}).strict(),
  z.object({operation:z.literal("apply"),token:id}).strict(),
  z.object({operation:z.literal("undo"),receipt:id}).strict(),
  z.object({operation:z.literal("history")}).strict(),
]);
export const restoreFields={requestId:id,threadId:z.string().min(1).max(1024),action:restoreAction};
export const restoreRequest=z.object({type:z.literal("desktop/restore"),...restoreFields}).strict();
export type RestoreAction=z.infer<typeof restoreAction>;
const action=z.enum(["restore","undo"]);
export const restoreResult=z.discriminatedUnion("kind",[
 z.object({kind:z.literal("preview"),token:id,path:baselinePath,action,effect:z.enum(["create","delete","replace"]),currentBytes:z.number().int().min(0).max(1048576),targetBytes:z.number().int().min(0).max(1048576)}).strict(),
 z.object({kind:z.literal("applied"),receipt:id,path:baselinePath,action}).strict(),
 z.object({kind:z.literal("history"),entries:z.array(z.object({id,path:baselinePath,action,state:z.enum(["prepared","applied","undo_prepared","undone"]),created:z.string().max(128)}).strict()).max(200)}).strict(),
]);
export type RestoreResult=z.infer<typeof restoreResult>;
export const restoreReply=z.object({type:z.literal("desktop/restoreResult"),requestId:id,viewId:z.string().min(1).max(1024),projectId:z.string().min(1).max(1024).nullable(),threadId:z.string().min(1).max(1024).nullable(),result:restoreResult.optional(),error:z.string().min(1).max(1024).optional()}).strict().refine(value=>!!value.result!==!!value.error);
/** Bound metadata before parsing; file bytes never cross into the renderer. */
export async function readRestoreResponse(response:Response):Promise<RestoreResult>{
 const reader=response.body?.getReader();if(!reader)throw new Error("File recovery returned no result. Check recovery history before trying again.");
 const chunks:Uint8Array[]=[];let length=0;
 try{while(true){const item=await reader.read();if(item.done)break;length+=item.value.byteLength;if(length>1024*1024)throw new Error("File recovery returned too much metadata. Check recovery history before trying again.");chunks.push(item.value);}}
 catch(error){await reader.cancel().catch(()=>{});throw error;}
 const bytes=new Uint8Array(length);let at=0;for(const chunk of chunks){bytes.set(chunk,at);at+=chunk.byteLength;}
 let raw:unknown;try{raw=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));}catch{throw new Error("File recovery returned invalid metadata. Check recovery history before trying again.");}
 if(!response.ok){const detail=(raw as {detail?:unknown})?.detail;throw new Error(typeof detail==="string"?detail.slice(0,1024):"File restore was refused. Refresh before retrying.");}
 try{return restoreResult.parse(raw);}catch{throw new Error("File recovery returned invalid metadata. Check recovery history before trying again.");}
}
