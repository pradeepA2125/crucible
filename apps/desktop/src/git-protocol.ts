import { z } from "zod";
import type { GitDiff, GitScope } from "./workspace-git.js";
export { isWorkspacePath } from "./workspace-path.js";
export const GIT_REPLY_BYTES = 16 * 1024 * 1024;
// Reader has two independent <=20k phases: unsupported tracked names, then valid tracked reads.
export const GIT_UNCOMPARED_LIMIT = 40000;
export class GitReplyTooLarge extends Error {
  constructor() { super("Git inspection exceeded the 16 MiB reply limit. Reload this view to retry."); }
}
export const gitGeneration = z.string().uuid();
const path = z.string().min(1).max(4096); // Display data; requests separately enforce path authority.
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const message = z.string().min(1).max(2048);
const mode = z.object({ from: z.string().regex(/^[0-7]{6}$/), to: z.enum(["100644", "100755"]) }).strict();
const entryType = z.object({ from: z.enum(["file", "link"]), to: z.enum(["file", "link"]) }).strict().refine(value => value.from !== value.to);
const facts = { mode: mode.optional(), type: entryType.optional() };
const change = z.object({ path, status: z.enum(["M", "A", "D", "R", "U"]), from: path.optional(), ...facts,
  note: z.enum(["attributes", "too-large"]).optional() }).strict().refine(value => value.status === "R" ? value.from !== undefined : value.from === undefined);
export const gitScopeResult = z.union([
  z.object({ state: z.literal("unavailable"), reason: message }).strict(),
  z.object({ state: z.literal("ready"), generation: gitGeneration, head: z.string().regex(/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/).optional(),
    changes: z.array(change).max(70000), uncompared: z.array(z.object({ path, reason: z.enum(["too-large", "unreadable", "unsupported"]) }).strict()).max(GIT_UNCOMPARED_LIMIT),
    partial: z.boolean(), rawBytes: z.literal(true), warnings: z.array(z.string().max(512)).max(32) }).strict(),
 ]).transform((value): GitScope => {
  if (value.state === "unavailable") return value;
  const { head, changes, ...rest } = value;
  return { ...rest, ...(head === undefined ? {} : { head }), changes: changes.map(change => {
    const { from, mode, type, note, ...entry } = change;
    return { ...entry, ...(from === undefined ? {} : { from }), ...(mode === undefined ? {} : { mode }),
      ...(type === undefined ? {} : { type }), ...(note === undefined ? {} : { note }) };
  }) };
}) satisfies z.ZodType<GitScope, z.ZodTypeDef, unknown>;
const diffFacts = { ...facts, fingerprint: z.string().regex(/^[0-9a-f]{64}$/).optional() };
const unified = z.string().max(4 * 1024 * 1024).refine(value => value.split("\n").length <= 3000 && new TextEncoder().encode(value).length <= 4 * 1024 * 1024);
export const gitDiffResult = z.union([
  z.object({ kind: z.literal("text"), unified, additions: count, deletions: count, truncated: z.boolean(), ...diffFacts }).strict(),
  z.object({ kind: z.literal("binary"), ...diffFacts }).strict(),
  z.object({ kind: z.literal("too-large") }).strict(),
  z.object({ kind: z.literal("unavailable"), reason: message }).strict(),
 ]).transform((value): GitDiff => {
  if (value.kind !== "text" && value.kind !== "binary") return value;
  const { mode, type, fingerprint, ...rest } = value;
  return { ...rest, ...(mode === undefined ? {} : { mode }), ...(type === undefined ? {} : { type }), ...(fingerprint === undefined ? {} : { fingerprint }) };
}) satisfies z.ZodType<GitDiff, z.ZodTypeDef, unknown>;
export type GitOperation = "scope" | "diff";
export type GitRequest = { requestId: string } & ({ type: "desktop/gitScope" } | { type: "desktop/gitDiff"; generation: string; path: string });
const transport = z.object({ status: z.enum(["busy", "timeout", "cancelled", "too-large", "error"]), message }).strict();
const base = { projectId: z.string().min(1).max(1024).nullable(), type: z.literal("desktop/gitResult"), requestId: z.string().min(1).max(1024) };
const scopeReply = z.object({ ...base, operation: z.literal("scope"), outcome: z.union([transport,
  z.object({ status: z.literal("ok"), result: gitScopeResult }).strict()]) }).strict();
const diffReply = z.object({ ...base, operation: z.literal("diff"), outcome: z.union([transport,
  z.object({ status: z.literal("ok"), result: gitDiffResult }).strict()]) }).strict();
export type GitReply = z.infer<typeof scopeReply> | z.infer<typeof diffReply>;
export function parseGitReply(value: unknown, operation: GitOperation): GitReply {
  const serialized = JSON.stringify(value);
  if (!serialized || serialized.length > GIT_REPLY_BYTES || new TextEncoder().encode(serialized).length > GIT_REPLY_BYTES) throw new GitReplyTooLarge();
  return operation === "scope" ? scopeReply.parse(value) : diffReply.parse(value);
}
