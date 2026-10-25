import { z } from "zod";
import { isWorkspacePath } from "./workspace-path.js";
// Browser-safe wire contract. No runtime import of host readers or credentials.
export const BASELINE_REPLY_BYTES = 4 * 1024 * 1024;
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const id = z.string().min(1).max(1024);
const message = z.string().min(1).max(512);
export const baselinePath = z.string().min(1).max(4096).refine(value => isWorkspacePath(value, false));
export const baselineIdentity = z.object({ seq: count, anchorMessageId: z.string().max(BASELINE_REPLY_BYTES), createdAt: z.string().max(BASELINE_REPLY_BYTES) }).strict();
const requestBase = { requestId: z.string().uuid(), threadId: id.nullable() };
export const baselineRequest = z.discriminatedUnion("type", [
  z.object({ ...requestBase, type: z.literal("desktop/baselineList") }).strict(),
  z.object({ ...requestBase, type: z.literal("desktop/baselineScope"), checkpoint: baselineIdentity }).strict(),
  z.object({ ...requestBase, type: z.literal("desktop/baselineDiff"), generation: z.string().uuid(), path: baselinePath }).strict(),
]);
export type BaselineRequest = z.infer<typeof baselineRequest>;
export type BaselineOperation = "list" | "scope" | "diff";
const unavailable = z.object({ state: z.literal("unavailable"), reason: message }).strict();
const change = z.object({ path: baselinePath, status: z.enum(["M", "A", "D"]), fromSeq: count, supplying: baselineIdentity }).strict()
  .refine(value => value.fromSeq === value.supplying.seq);
export const baselineScopeResult = z.union([unavailable, z.object({ state: z.literal("ready"), generation: z.string().uuid(), checkpoint: baselineIdentity,
  changes: z.array(change).max(500), unchanged: count, createdRemoved: count,
  // Unsupported filenames are display data, bounded by the whole envelope.
  uncompared: z.array(z.object({ path: z.string().max(BASELINE_REPLY_BYTES), reason: message }).strict()).max(500), omitted: count, partial: z.boolean(),
}).strict().refine(value => value.changes.length + value.uncompared.length + value.unchanged + value.createdRemoved <= 500
  && value.changes.every(entry => entry.fromSeq >= value.checkpoint.seq)
  && new Set([...value.changes, ...value.uncompared].map(entry => entry.path)).size === value.changes.length + value.uncompared.length
  && value.partial === (value.omitted > 0 || value.uncompared.length > 0))]);
export const baselineListResult = z.union([unavailable, z.object({ state: z.literal("ready"), checkpoints: z.array(baselineIdentity).max(200), omitted: count }).strict()
  .refine(value => new Set(value.checkpoints.map(entry => entry.seq)).size === value.checkpoints.length)]);
const fingerprint = z.string().regex(/^[0-9a-f]{64}$/);
const unified = z.string().max(BASELINE_REPLY_BYTES).refine(value => value.split("\n").length <= 3000 && new TextEncoder().encode(value).length <= BASELINE_REPLY_BYTES);
export const baselineDiffResult = z.union([
  z.object({ kind: z.literal("unavailable"), reason: message }).strict(), z.object({ kind: z.literal("too-large") }).strict(),
  z.object({ kind: z.literal("binary"), fingerprint }).strict(),
  z.object({ kind: z.literal("text"), unified, additions: count, deletions: count, truncated: z.boolean(), fingerprint }).strict(),
]);
const transport = z.object({ status: z.enum(["busy", "timeout", "cancelled", "error", "too-large"]), message }).strict();
const base = { type: z.literal("desktop/baselineResult"), requestId: z.string().uuid(), viewId: id, projectId: id.nullable(), threadId: id.nullable() };
const listReply = z.object({ ...base, operation: z.literal("list"), outcome: z.union([transport, z.object({ status: z.literal("ok"), result: baselineListResult }).strict()]) }).strict();
const scopeReply = z.object({ ...base, operation: z.literal("scope"), outcome: z.union([transport, z.object({ status: z.literal("ok"), result: baselineScopeResult }).strict()]) }).strict();
const diffReply = z.object({ ...base, operation: z.literal("diff"), generation: z.string().uuid(), path: baselinePath,
  outcome: z.union([transport, z.object({ status: z.literal("ok"), result: baselineDiffResult }).strict()]) }).strict();
export type BaselineReply = z.infer<typeof listReply> | z.infer<typeof scopeReply> | z.infer<typeof diffReply>;
export class BaselineReplyTooLarge extends Error { constructor() { super("Checkpoint comparison is too large to send. Choose another checkpoint."); } }
export function parseBaselineReply(value: unknown, operation: BaselineOperation): BaselineReply {
  const serialized = JSON.stringify(value);
  if (!serialized || serialized.length > BASELINE_REPLY_BYTES || new TextEncoder().encode(serialized).length > BASELINE_REPLY_BYTES) throw new BaselineReplyTooLarge();
  return operation === "list" ? listReply.parse(value) : operation === "scope" ? scopeReply.parse(value) : diffReply.parse(value);
}
