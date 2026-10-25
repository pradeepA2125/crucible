import { createHash, randomUUID } from "node:crypto";
import { createTwoFilesPatch } from "diff";
import type { BackendTaskClient, BaselineEntry, BaselineFile, CheckpointSummary } from "@crucible/editor-client";
import { createBaselineReadOperations, BaselineReadStopped, type BaselineReadOptions, type BaselineReadOperation } from "./baseline-read-operation.js";
import { WorkspaceFileError, type createWorkspaceFiles } from "./workspace-files.js";
import { isWorkspacePath } from "./workspace-path.js";
export type CheckpointIdentity = Pick<CheckpointSummary, "seq" | "anchorMessageId" | "createdAt">;
export interface BaselineChange { path: string; status: "M" | "A" | "D"; fromSeq: number; supplying: CheckpointIdentity; }
export type BaselineScope = { state: "unavailable"; reason: string } | {
  state: "ready"; generation: string; checkpoint: CheckpointIdentity; changes: BaselineChange[];
  unchanged: number; createdRemoved: number; uncompared: { path: string; reason: string }[]; omitted: number; partial: boolean;
};
export type BaselineDiff = { kind: "unavailable"; reason: string } | { kind: "too-large" } |
  { kind: "binary"; fingerprint: string } | { kind: "text"; unified: string; additions: number; deletions: number; truncated: boolean; fingerprint: string };
type Files = Pick<Awaited<ReturnType<typeof createWorkspaceFiles>>, "compareBytes">;
type Backend = Pick<BackendTaskClient, "listCheckpoints" | "getCheckpointBaseline" | "getCheckpointBaselineFile">;
interface Member { entry: BaselineEntry; supplying: CheckpointIdentity; status: BaselineChange["status"]; }
interface Generation { thread: string; checkpoint: CheckpointIdentity; members: Map<string, Member>; }
class BaselineUnavailable extends Error {}
class BaselineTooLarge extends BaselineUnavailable {}
const unavailable = () => new BaselineUnavailable("This checkpoint changed or was removed. Refresh.");
const identity = (value: CheckpointIdentity): CheckpointIdentity => ({ seq: value.seq, anchorMessageId: value.anchorMessageId, createdAt: value.createdAt });
const same = (a: CheckpointIdentity, b: CheckpointIdentity) => a.seq === b.seq && a.anchorMessageId === b.anchorMessageId && a.createdAt === b.createdAt;
const equalEntry = (a: BaselineEntry, b: BaselineEntry) => a.path === b.path && a.fromSeq === b.fromSeq && a.existed === b.existed && a.oversize === b.oversize;
const safeReason = (error: unknown, fallback: string) => {
  if (error instanceof WorkspaceFileError || error instanceof BaselineUnavailable) return error.message.slice(0,512);
  if (error instanceof Error && ["Retained checkpoint response is too large to inspect.", "Retained file is too large to compare here (1 MiB limit)."].includes(error.message)) return error.message;
  return fallback;
};
const fileLimit = 1024 * 1024, contentLimit = 32 * 1024 * 1024;
const decode = (bytes: Uint8Array) => { if (bytes.includes(0)) return undefined; try { return new TextDecoder("utf-8", {fatal:true}).decode(bytes); } catch { return undefined; } };
/** Host-internal retained edit-tool evidence; no mutation, filesystem root or renderer authority. */
export function createWorkspaceBaseline(files: Files, backend: Backend, now: () => number = () => performance.now()) {
  const perform = createBaselineReadOperations(), generations = new Map<string, Generation>();
  const list = async (thread: string, op: BaselineReadOperation) => {
    if (!backend.listCheckpoints) throw new BaselineUnavailable("Retained checkpoints aren't available for this conversation.");
    const values = await op.http(signal => backend.listCheckpoints!(thread, signal));
    const seqs = new Set<number>();
    for (const value of values) { if (!Number.isSafeInteger(value.seq) || value.seq < 0 || seqs.has(value.seq)) throw unavailable(); seqs.add(value.seq); }
    return values;
  };
  const verify = (values: CheckpointSummary[], chosen: CheckpointIdentity) => {
    const match = values.find(value => value.seq === chosen.seq); if (!match || !same(match, chosen)) throw unavailable(); return match;
  };
  const fold = async (thread: string, chosen: CheckpointIdentity, op: BaselineReadOperation) => {
    if (!backend.getCheckpointBaseline) throw unavailable();
    const entries = await op.http(signal => backend.getCheckpointBaseline!(thread, chosen.seq, signal)), paths = new Set<string>();
    for (const entry of entries) { if (paths.has(entry.path) || entry.fromSeq < chosen.seq) throw unavailable(); paths.add(entry.path); }
    return entries;
  };
  const pair = async (thread: string, chosen: CheckpointIdentity, entry: BaselineEntry, op: BaselineReadOperation) => {
    if (!backend.getCheckpointBaselineFile) throw unavailable();
    if (entry.oversize) throw new BaselineTooLarge("Retained content is too large to compare here.");
    const old: BaselineFile = await op.http(signal => backend.getCheckpointBaselineFile!(thread, chosen.seq, entry.path, signal));
    if (old.path !== entry.path || old.fromSeq !== entry.fromSeq) throw unavailable();
    if (old.state === "too_large" || old.state === "oversize") throw new BaselineTooLarge("Retained content is too large to compare here.");
    if (old.state !== "captured" && old.state !== "absent") throw new BaselineUnavailable("Retained content is unavailable.");
    if (entry.existed !== (old.state === "captured")) throw unavailable();
    const before = old.state === "captured" ? old.content : new Uint8Array();
    if (before.byteLength > fileLimit) throw new BaselineTooLarge("Retained content is too large to compare here.");
    let after = new Uint8Array(), exists = false;
    try {
      const current = await op.file(() => files.compareBytes(entry.path, fileLimit));
      if (current.truncated || current.size > fileLimit) throw new BaselineTooLarge("Current content is too large to compare here.");
      after = current.content; exists = true;
    } catch (error) { op.check(); if (!(error instanceof WorkspaceFileError && error.reason === "missing")) throw error; }
    return { before, after, exists };
  };
  const listCheckpoints = (thread: string, options: BaselineReadOptions = {}) => perform(30000, options, async op => {
    try { return {state:"ready" as const, checkpoints:await list(thread, op)}; }
    catch (error) { op.check(); if (error instanceof BaselineReadStopped) throw error; return {state:"unavailable" as const, reason:safeReason(error,"Retained checkpoints aren't available for this conversation.")}; }
  });
  const scope = (thread: string, requested: CheckpointIdentity, options: BaselineReadOptions = {}): Promise<BaselineScope> => {
    const chosen = identity(requested);
    return perform(30000, options, async op => {
      const started = now();
      try {
        const checkpoints = await list(thread, op); verify(checkpoints, chosen);
        const entries = await fold(thread, chosen, op), members = new Map<string, Member>(), changes: BaselineChange[] = [], uncompared: {path:string;reason:string}[] = [];
        const supplyingFacts = new Map<number,CheckpointIdentity>();
        let unchanged = 0, createdRemoved = 0, bytes = 0, inspected = 0;
        for (const entry of entries.slice(0, 500)) {
          op.check(); if (now() - started >= 10000 || bytes + 2 * fileLimit > contentLimit) break;
          inspected++;
          if (!isWorkspacePath(entry.path, false)) { uncompared.push({path:entry.path,reason:"This filename cannot be inspected."}); continue; }
          try {
            const found = checkpoints.find(value => value.seq === entry.fromSeq); if (!found) throw unavailable();
            const supplying = identity(found); supplyingFacts.set(supplying.seq,supplying);
            const value = await pair(thread, chosen, entry, op); bytes += value.before.byteLength + value.after.byteLength;
            if (entry.existed && value.exists && Buffer.from(value.before).equals(value.after)) { unchanged++; continue; }
            if (!entry.existed && !value.exists) { createdRemoved++; continue; }
            const status = !entry.existed ? "A" : !value.exists ? "D" : "M";
            members.set(entry.path, {entry:{...entry},supplying:identity(supplying),status}); changes.push({path:entry.path,status,fromSeq:entry.fromSeq,supplying:identity(supplying)});
          } catch (error) { op.check(); if (error instanceof BaselineReadStopped) throw error; uncompared.push({path:entry.path,reason:safeReason(error,"This file could not be compared.")}); }
        }
        const confirmed = await list(thread, op); verify(confirmed, chosen); for (const supplying of supplyingFacts.values()) verify(confirmed, supplying);
        op.check(); const generation = randomUUID(), omitted = entries.length - inspected;
        generations.set(generation,{thread,checkpoint:chosen,members}); while (generations.size>2) generations.delete(generations.keys().next().value!);
        return {state:"ready",generation,checkpoint:identity(chosen),changes,unchanged,createdRemoved,uncompared,omitted,partial:!!omitted || !!uncompared.length};
      } catch (error) { op.check(); if (error instanceof BaselineReadStopped) throw error; return {state:"unavailable",reason:safeReason(error,"Retained checkpoints aren't available for this conversation.")}; }
    });
  };
  const diff = (generation: string, path: string, options: BaselineReadOptions = {}): Promise<BaselineDiff> => perform(15000, options, async op => {
    const captured = generations.get(generation), member = captured?.members.get(path);
    if (!isWorkspacePath(path,false) || !captured || !member) return {kind:"unavailable",reason:"This comparison is out of date. Refresh."};
    try {
      const checkpoints = await list(captured.thread, op); verify(checkpoints,captured.checkpoint); verify(checkpoints,member.supplying);
      const entries = await fold(captured.thread,captured.checkpoint,op), latest = entries.find(entry => entry.path === path);
      if (!latest || !equalEntry(latest,member.entry)) throw unavailable();
      const value = await pair(captured.thread,captured.checkpoint,latest,op);
      const status = !latest.existed ? value.exists ? "A" : undefined : !value.exists ? "D" : Buffer.from(value.before).equals(value.after) ? undefined : "M";
      if (status !== member.status) throw new BaselineUnavailable("This file changed since the comparison. Refresh.");
      const fingerprint = createHash("sha256").update(JSON.stringify([captured.thread,path,captured.checkpoint,member.supplying,status,value.before.byteLength,value.after.byteLength])).update(value.before).update(value.after).digest("hex");
      const before = decode(value.before), after = decode(value.after); if (before === undefined || after === undefined) return {kind:"binary",fingerprint};
      const patch = createTwoFilesPatch(`a/${path}`,`b/${path}`,before,after,undefined,undefined,{context:3,maxEditLength:20000,timeout:2000}); op.check();
      if (patch === undefined) return {kind:"too-large"};
      const lines=patch.split("\n"), start=lines.findIndex(line=>line.startsWith("@@")), body=start<0?[]:lines.slice(start).filter((line,index,all)=>line||index<all.length-1);
      const shown:string[]=[];let size=0;
      for(const line of body) { const extra=Buffer.byteLength(line,"utf8")+(shown.length?1:0); if(shown.length>=3000||size+extra>4*1024*1024) break;shown.push(line);size+=extra; }
      return {kind:"text",unified:shown.join("\n"),additions:body.filter(line=>line.startsWith("+")).length,deletions:body.filter(line=>line.startsWith("-")).length,truncated:shown.length<body.length,fingerprint};
    } catch(error) { op.check();if(error instanceof BaselineReadStopped)throw error;if(error instanceof BaselineTooLarge)return {kind:"too-large"};return {kind:"unavailable",reason:safeReason(error,"Retained comparison is unavailable. Refresh.")}; }
  });
  return {listCheckpoints,scope,diff};
}
