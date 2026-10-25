import { baselineIdentity } from "../../../../desktop/src/baseline-protocol";
import type { CheckpointIdentity, BaselineScope } from "../../../../desktop/src/workspace-baseline";
import type { GitScope } from "../../../../desktop/src/workspace-git";
import { BaselineInspectionError, type BaselineIdentity, type BaselinePort } from "./baselineClient";
import { ChangesInspectionError, type ChangesPort } from "./changesClient";
const same=(a:CheckpointIdentity|undefined,b:CheckpointIdentity|undefined)=>a?.seq===b?.seq&&a?.anchorMessageId===b?.anchorMessageId&&a?.createdAt===b?.createdAt;
const identityKey=(value:BaselineIdentity|null)=>JSON.stringify(value?[value.viewId,value.projectId,value.threadId]:null);
export interface BaselineReviewAdapter {
  review:ChangesPort;choose(checkpoint?:CheckpointIdentity):void;selected():CheckpointIdentity|undefined;result():BaselineScope|undefined;
  onChange(callback:()=>void):()=>void;dispose():void;
}
/** Reuse the existing bounded diff cache / loading / Viewed hook, without treating retained evidence as Git facts. */
export function createBaselineReviewAdapter(port:BaselinePort):BaselineReviewAdapter {
  let context=port.context(),chosen:CheckpointIdentity|undefined,result:BaselineScope|undefined,revision=0,closed=false,unsubscribe:(()=>void)|undefined;
  const projects=new Set<(key:string|null)=>void>(),listeners=new Set<()=>void>();
  const key=()=>context?.projectId&&context.threadId?JSON.stringify([context.viewId,context.projectId,context.threadId,chosen??null]):null;
  const notify=()=>{for(const callback of projects)callback(key());for(const callback of listeners)callback();};
  const sync=(next:BaselineIdentity|null)=>{if(identityKey(context)===identityKey(next))return;context=next?{...next}:null;chosen=undefined;result=undefined;revision++;notify();};
  // No subscription in construction/render: StrictMode may discard this object.
  const attach=()=>{if(!unsubscribe&&!closed){unsubscribe=port.onContextChange(sync);sync(port.context());}};
  const detach=()=>{if(!projects.size&&!listeners.size){unsubscribe?.();unsubscribe=undefined;}};
  const translate=(reason:unknown):never=>{if(reason instanceof BaselineInspectionError)throw new ChangesInspectionError(reason.reason,reason.message);throw reason;};
  const dispose=()=>{closed=true;revision++;unsubscribe?.();unsubscribe=undefined;projects.clear();listeners.clear();};
  const review:ChangesPort={
    project:key,onProjectChange:callback=>{if(!closed){projects.add(callback);attach();}return()=>{projects.delete(callback);detach();};},dispose,
    scope:async()=>{
      sync(port.context());if(closed)throw new ChangesInspectionError("cancelled","Checkpoint inspection cancelled.");
      if(!chosen)return{state:"unavailable",reason:"Choose a retained checkpoint to compare agent edits."};
      const captured=revision,selection={...chosen};result=undefined;for(const callback of listeners)callback();
      try{
        const value=await port.scope(selection);
        if(closed||captured!==revision)throw new ChangesInspectionError("cancelled","The checkpoint or conversation changed.");
        result=value;for(const callback of listeners)callback();
        if(value.state==="unavailable")return value;
        // Rendering-model fields only. Full uncompared reasons/counts/provenance
        // remain in result(); do not invent a Git reason, HEAD or branch.
        const mapped:GitScope={state:"ready",generation:value.generation,changes:value.changes.map(entry=>({path:entry.path,status:entry.status})),
          partial:value.partial,rawBytes:true,warnings:[],uncompared:[]};
        return mapped;
      }catch(reason){return translate(reason);}
    },
    diff:async(generation,path)=>{const captured=revision;try{const value=await port.diff(generation,path);if(closed||captured!==revision)throw new ChangesInspectionError("cancelled","The checkpoint or conversation changed.");return value;}catch(reason){return translate(reason);}},
  };
  return {review,selected:()=>chosen?{...chosen}:undefined,result:()=>result,
    choose:checkpoint=>{sync(port.context());if(closed)return;const next=checkpoint===undefined?undefined:baselineIdentity.parse(checkpoint);if(same(chosen,next))return;chosen=next;result=undefined;revision++;notify();},
    onChange:callback=>{if(!closed){listeners.add(callback);attach();}return()=>{listeners.delete(callback);detach();};},dispose,
  };
}
