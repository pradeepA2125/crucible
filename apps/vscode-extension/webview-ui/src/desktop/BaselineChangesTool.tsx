import { RestoreFile,FileRecoveryHistory } from "./RestoreFile";
import { useMemo, type ReactNode } from "react";
import type { ChatMsg } from "../types";
import type { CheckpointIdentity } from "../../../../desktop/src/workspace-baseline";
import { GitChangesTool, type ComparisonPresentation } from "./GitChangesTool";
import type { BaselineReviewModel } from "./useBaselineChanges";
export function checkpointLabel(checkpoint:CheckpointIdentity,anchors:ReadonlyMap<string,string>):string {
  const time=new Date(checkpoint.createdAt.slice(0,128));
  const date=Number.isFinite(time.getTime())?time.toLocaleString(undefined,{dateStyle:"short",timeStyle:"medium"}):"Saved checkpoint";
  const anchor=anchors.get(checkpoint.anchorMessageId);return anchor?`Before your message “${anchor}” · ${date}`:date;
}
export function BaselineChangesTool({model,messages,scope}:{model:BaselineReviewModel;messages:ChatMsg[];scope?:ReactNode}) {
  const anchors=useMemo(()=>new Map(messages.filter(message=>message.role==="user"&&!!message.id).map(message=>{
    const line=message.content.trimStart().split(/\r?\n/,1)[0]!;return [message.id!,line.length>70?line.slice(0,70)+"…":line];
  })),[messages]);
  const list=model.listing?.state==="ready"?model.listing:undefined;
  const chosen=model.checkpoint,index=list?.checkpoints.findIndex(entry=>entry.seq===chosen?.seq&&entry.anchorMessageId===chosen.anchorMessageId&&entry.createdAt===chosen.createdAt)??-1;
  const facts=model.facts?.state==="ready"&&model.result?.state==="ready"&&model.facts.generation===model.result.generation?model.facts:undefined;
  const presentation:ComparisonPresentation={
    commentSource:path=>{ const member=facts?.changes.find(entry=>entry.path===path);return facts&&member?{identity:JSON.stringify([facts.generation,facts.checkpoint,member.supplying,path]),label:`Agent edits since ${checkpointLabel(facts.checkpoint,anchors)} · retained content supplied by ${checkpointLabel(member.supplying,anchors)}`} : undefined; },
    title:"Files edited by the agent since a checkpoint",refreshLabel:"Refresh checkpoint comparison",
    referenceControl:<select aria-label="Retained checkpoint" disabled={model.loading||!list?.checkpoints.length} value={index<0?"":String(index)} onChange={event=>{if(!event.target.value)return;const entry=list?.checkpoints[Number(event.target.value)];if(entry)void model.choose(entry);}}>
      <option value="">Choose a checkpoint…</option>{list?.checkpoints.map((entry,index)=><option key={entry.seq} value={String(index)}>{checkpointLabel(entry,anchors)}</option>)}
    </select>,
    notice:<>{model.refreshFailed&&<strong role="status">Showing the comparison from {model.capturedAt===undefined?"earlier":new Date(model.capturedAt).toLocaleString()}; refresh didn't finish. </strong>}<span>Changes made by commands or outside the agent's edits aren't tracked here; use Uncommitted vs HEAD for the whole working tree.</span></>,
    summary:facts?`${facts.changes.length} changed · ${facts.changes.filter(entry=>model.isViewed(entry.path)).length} viewed · ${facts.unchanged} unchanged · ${facts.createdRemoved} created then removed · ${facts.uncompared.length} not compared · ${facts.omitted} omitted`:"Files edited by the agent since a checkpoint.",
    loadingText:model.listLoading?"Loading retained checkpoints…":"Comparing agent-edit files…",allFilesText:"Agent-edit files · scroll to review all files",
    idleText:"Choose a retained checkpoint to compare agent edits.",idleInformation:chosen?"Agent-edit comparison unavailable.":"Choose a retained checkpoint to begin.",unavailableInformation:"Agent-edit comparison unavailable.",emptyInformation:facts?.partial?"Partial agent-edit comparison · no changes confirmed":"Agent-edit comparison · no changes confirmed",emptyText:"No agent-edit content differences were confirmed.",partialEmptyText:"No changes were confirmed in the inspected agent-edit files.",
    notes:<>{model.capturedAt!==undefined&&<p>Compared at {new Date(model.capturedAt).toLocaleString()}.</p>}{facts&&<><p>{facts.unchanged} agent-edited files match their retained content.</p><p>{facts.createdRemoved} created-then-removed paths are not listed as changed.</p>{facts.omitted>0&&<p role="status">{facts.omitted} agent-edited paths were not inspected within the comparison limits.</p>}
      {!!facts.uncompared.length&&<details><summary>{facts.uncompared.length} files not compared</summary><ul>{facts.uncompared.slice(0,50).map(entry=><li key={entry.path}>{entry.path} — {entry.reason}</li>)}</ul>{facts.uncompared.length>50&&<p>Showing the first 50 paths.</p>}</details>}</>}
      {!!list?.omitted&&<p>{list.omitted} older checkpoints are not listed.</p>}<p>Retained pre-edit content is compared with current safe file reads. No rename, mode or branch facts are inferred.</p></>,
    fileNote:path=>{const member=facts?.changes.find(entry=>entry.path===path),detail=model.details?.get(path)??(model.detail?.path===path?model.detail:undefined),diff=detail?.result;
      const action=facts&&member&&diff?.kind==="text"&&!diff.truncated&&diff.fingerprint?{operation:"preview" as const,path,checkpoint:facts.checkpoint,supplying:member.supplying,fingerprint:diff.fingerprint}:undefined;
      return member?<div className="review-explanation"><p>Retained pre-edit content supplied by {checkpointLabel(member.supplying,anchors)}</p><RestoreFile key={JSON.stringify([model.identity,path,diff&&"fingerprint" in diff?diff.fingerprint:""])} run={model.restore} path={path} action={action} onChanged={()=>void model.refresh()}/></div>:null;},
  };
  return <>
    {!model.loading&&!list&&<div className="review-explanation"><button type="button" onClick={()=>void model.open()}>Load retained checkpoints</button></div>}
    {model.listing?.state==="unavailable"&&<p role="status" className="review-explanation">{model.listing.reason}</p>}
    {list&&!list.checkpoints.length&&<p role="status" className="review-explanation">No retained checkpoints are listed for this conversation.</p>}
    <GitChangesTool model={model} scope={scope} presentation={presentation}/>
    <FileRecoveryHistory key={model.identity} latest={model.latestRecovery} run={model.restore} onChanged={()=>void model.refresh()}/>
  </>;
}
