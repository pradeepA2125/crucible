import { BaselineReadStopped } from "./baseline-read-operation.js";
import { BaselineReplyTooLarge, baselineRequest, parseBaselineReply, type BaselineOperation, type BaselineReply, type BaselineRequest } from "./baseline-protocol.js";
import type { createWorkspaceBaseline } from "./workspace-baseline.js";
type Reader = ReturnType<typeof createWorkspaceBaseline>;
export interface BaselineContext { viewId: string; projectId: string | null; threadId: string | null; }
const same = (a: BaselineContext, b: BaselineContext) => a.viewId === b.viewId && a.projectId === b.projectId && a.threadId === b.threadId;
const checkpoint = ({seq, anchorMessageId, createdAt}: {seq:number;anchorMessageId:string;createdAt:string}) => ({seq,anchorMessageId,createdAt});
/** Thread-bound metadata and diff lanes. Logical cancellation never releases reader physical admission. */
export function createWorkspaceBaselineRequests(reader: Reader, identity: () => BaselineContext, post: (reply: BaselineReply) => void) {
  const active = new Map<"metadata" | "diff", AbortController>();
  const generations = new Map<string, { threadId: string; paths: Set<string> }>();
  let stopped = false;
  const handle = async (raw: BaselineRequest): Promise<void> => {
    if (stopped) return;
    const request = baselineRequest.parse(raw), captured = { ...identity() };
    const operation: BaselineOperation = request.type === "desktop/baselineList" ? "list" : request.type === "desktop/baselineScope" ? "scope" : "diff";
    const lane = operation === "diff" ? "diff" : "metadata";
    let replied = false;
    const reply = (outcome: unknown): BaselineReply | undefined => {
      if (replied) return; replied = true;
      if (stopped || identity().viewId !== captured.viewId) return;
      const base = {type:"desktop/baselineResult",requestId:request.requestId,...captured,operation,
        ...(request.type === "desktop/baselineDiff" ? {generation:request.generation,path:request.path} : {})};
      const finalOutcome = same(captured, identity()) ? outcome : {status:"cancelled",message:"The conversation or project changed. Reopen Changes."};
      let value: BaselineReply;
      try {
        value = parseBaselineReply({...base,outcome:finalOutcome},operation);
        if (request.type === "desktop/baselineScope" && value.operation === "scope" && value.outcome.status === "ok" && value.outcome.result.state === "ready") {
          const actual = value.outcome.result.checkpoint, expected = request.checkpoint;
          if (actual.seq !== expected.seq || actual.anchorMessageId !== expected.anchorMessageId || actual.createdAt !== expected.createdAt) throw new Error("Checkpoint identity mismatch.");
        }
      }
      catch (error) { value = parseBaselineReply({...base,outcome:error instanceof BaselineReplyTooLarge ? {status:"too-large",message:error.message}
        : {status:"error",message:"Checkpoint inspection returned an invalid result. Refresh to retry."}},operation); }
      post(value); return value;
    };
    if (request.threadId !== captured.threadId) { reply({status:"cancelled",message:"The conversation changed. Reopen Changes."}); return; }
    if (!captured.projectId || !captured.threadId) {
      const reason = "Start or open a conversation to compare checkpoints.";
      reply({status:"ok",result: operation === "diff" ? {kind:"unavailable",reason} : {state:"unavailable",reason}}); return;
    }
    if (request.type === "desktop/baselineDiff") {
      const known = generations.get(request.generation);
      if (!known || known.threadId !== captured.threadId || !known.paths.has(request.path)) {
        reply({status:"ok",result:{kind:"unavailable",reason:"This comparison is out of date. Refresh."}}); return;
      }
    }
    if (active.has(lane)) { reply({status:"busy",message:"A checkpoint inspection is already running. Retry when it finishes."}); return; }
    const controller = new AbortController(); active.set(lane,controller);
    const milliseconds = operation === "diff" ? 15000 : 30000;
    let timedOut = false;
    const timer = setTimeout(() => {timedOut=true;controller.abort();},milliseconds);
    try {
      const result = await new Promise<unknown>((resolve,reject) => {
        const aborted = () => reject(new BaselineReadStopped(timedOut ? "timeout" : "cancelled"));
        controller.signal.addEventListener("abort",aborted,{once:true});
        Promise.resolve().then<unknown>(() => {
          if (controller.signal.aborted) throw new BaselineReadStopped(timedOut ? "timeout" : "cancelled");
          const options = {signal:controller.signal};
          if(request.type === "desktop/baselineList") return reader.listCheckpoints(captured.threadId!,options).then(value => value.state === "unavailable" ? value
            : {state:"ready",checkpoints:[...value.checkpoints].sort((a,b)=>b.seq-a.seq).slice(0,200).map(checkpoint),omitted:Math.max(0,value.checkpoints.length-200)});
          if(request.type === "desktop/baselineScope") return reader.scope(captured.threadId!,request.checkpoint,options);
          return reader.diff(request.generation,request.path,options);
        }).then(resolve,reject).finally(() => controller.signal.removeEventListener("abort",aborted));
      });
      const sent = reply({status:"ok",result});
      if (sent?.operation === "scope" && sent.outcome.status === "ok" && sent.outcome.result.state === "ready") {
        generations.set(sent.outcome.result.generation,{threadId:captured.threadId,paths:new Set(sent.outcome.result.changes.map(entry => entry.path))});
        while(generations.size>2) generations.delete(generations.keys().next().value!);
      }
    } catch(error) {
      reply(error instanceof BaselineReadStopped ? {status:timedOut ? "timeout" : error.reason,message:error.message}
        : {status:"error",message:"Checkpoint inspection failed. Refresh to retry."});
    } finally { clearTimeout(timer);if(active.get(lane)===controller)active.delete(lane); }
  };
  const cancel = () => {generations.clear();for(const controller of active.values())controller.abort();};
  return {handle,cancel,dispose:()=>{stopped=true;cancel();}};
}
