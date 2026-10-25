import type { RestoreAction,RestoreResult } from "../../../../desktop/src/restore-protocol";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { CheckpointIdentity } from "../../../../desktop/src/workspace-baseline";
import type { BaselineList, BaselinePort } from "./baselineClient";
import { createBaselineReviewAdapter } from "./baselineReviewAdapter";
import { useGitChanges } from "./useGitChanges";
const same=(a:CheckpointIdentity|undefined,b:CheckpointIdentity|undefined)=>a?.seq===b?.seq&&a?.anchorMessageId===b?.anchorMessageId&&a?.createdAt===b?.createdAt;
export function useBaselineChanges(port?:BaselinePort,openFile?:(path:string)=>void) {
  const adapter=useMemo(()=>port?createBaselineReviewAdapter(port):undefined,[port]);
  const review=useGitChanges(adapter?.review,openFile);
  const [,notify]=useReducer(value=>value+1,0);
  const [listing,setListing]=useState<BaselineList>(),[listLoading,setListLoading]=useState(false),[listError,setListError]=useState("");
  const [latestRecovery,setLatestRecovery]=useState<Extract<RestoreResult,{kind:"applied"}>>();
  const restore=useMemo(()=>port?.restore?async(action:RestoreAction)=>{const value=await port.restore!(action);if(value.kind==="applied")setLatestRecovery(value);return value;}:undefined,[port]);
  const epoch=useRef(0);
  const loaded=useRef<{identity:string;value:BaselineList}>();
  const [refreshFailed,setRefreshFailed]=useState(false);
  useEffect(()=>{
    const reset=()=>{setLatestRecovery(undefined);epoch.current++;loaded.current=undefined;setListing(undefined);setListLoading(false);setListError("");setRefreshFailed(false);};reset();
    const off=adapter?.onChange(notify),offContext=port?.onContextChange(reset);
    return()=>{epoch.current++;off?.();offContext?.();};
  },[port,adapter]);
  const loadList=useCallback(async(refresh=false)=>{
    if(!port)return;const current=++epoch.current,identity=JSON.stringify(port.context());setListLoading(true);setListError("");loaded.current=undefined;
    try{const value=await port.list(refresh);if(current!==epoch.current||identity!==JSON.stringify(port.context()))return;loaded.current={identity,value};setListing(value);return value;}
    catch(reason){if(current===epoch.current&&identity===JSON.stringify(port.context())){setListing(undefined);setListError(reason instanceof Error?reason.message:"Retained checkpoints aren't available for this conversation.");}}
    finally{if(current===epoch.current)setListLoading(false);}
  },[port]);
  const choose=useCallback(async(checkpoint:CheckpointIdentity)=>{
    if(!adapter||!port||loaded.current?.identity!==JSON.stringify(port.context())||loaded.current?.value!==listing||listing?.state!=="ready"||!listing.checkpoints.some(value=>same(value,checkpoint))){setListError("Choose a checkpoint from this conversation's current list.");return;}
    setListError("");setRefreshFailed(false);adapter.choose(checkpoint);await review.refresh();
  },[adapter,port,listing,review.refresh]);
  const refresh=useCallback(async()=>{
    setRefreshFailed(false);const selected=adapter?.selected(),value=await loadList(true);
    if(!adapter||!same(selected,adapter.selected()))return;
    if(!value){if(selected&&review.result?.state==="ready")setRefreshFailed(true);return;}
    if(value.state!=="ready"){adapter.choose(undefined);return;}
    if(selected&&!value.checkpoints.some(entry=>same(entry,selected))){adapter.choose(undefined);setListError("This checkpoint changed or was removed. Choose another checkpoint.");return;}
    if(selected)await review.refresh();
  },[adapter,loadList,review.refresh,review.result]);
  return {...review,restore,latestRecovery,identity:JSON.stringify(port?.context()),enabled:!!port,loading:review.loading||listLoading,error:listError||review.error,refresh,open:loadList,choose,listing,listLoading,
    refreshFailed,checkpoint:adapter?.selected(),facts:adapter?.result()};
}
export type BaselineReviewModel=ReturnType<typeof useBaselineChanges>;
