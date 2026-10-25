import type { ChatMsg, DiffEntry } from "../types";
import type { RecordedEditSource } from "../transcript/groupTranscript";
import { hash32 } from "../utils/hash";

/** Appended by the backend (patch/diffing.py cap_unified_diff) when it shortened a recorded diff. */
export const DIFF_TRUNCATION_MARKER = "… diff truncated — open in editor for the full diff";
export const CHANGES_FILE_LIMIT = 500;
const MAX_DIFF_LINES = 400, MAX_DIFF_CHARS = 24000;
export interface ConversationEdit { key: string; signature: string; turn: number; additions: number; deletions: number; diff?: string; truncated: boolean; }
export interface ConversationChange { path: string; additions: number; deletions: number; edits: ConversationEdit[]; signature: string; }
export interface ConversationChanges { files: ConversationChange[]; omitted: number; }
const count = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;

interface CachedEdit extends Omit<ConversationEdit, "key" | "turn"> { path: string; position: number; signature: string; }
// Reducer updates replace changed messages/metadata; untouched card objects are immutable.
// Weak keys allow removed conversations and their bounded diff text to be collected.
const evidenceCache = new WeakMap<ChatMsg, CachedEdit[]>();
function evidenceOf(msg: ChatMsg): CachedEdit[] {
  const cached = evidenceCache.get(msg);
  if (cached) return cached;
  const entries = Array.isArray(msg.metadata.diff_entries) ? msg.metadata.diff_entries as Partial<DiffEntry>[] : [];
  const edits: CachedEdit[] = [];
  entries.forEach((entry, position) => {
    if (!entry || typeof entry.path !== "string" || !entry.path) return;
    const raw = typeof entry.unified_diff === "string" ? entry.unified_diff : undefined;
    const recordedTruncation = !!raw?.endsWith(DIFF_TRUNCATION_MARKER);
    const recorded = recordedTruncation ? raw!.slice(0, -DIFF_TRUNCATION_MARKER.length).replace(/\n$/, "") : raw;
    // Older backends may not cap their stored diffs; keep this review's DOM bounded too.
    const head = recorded?.slice(0, MAX_DIFF_CHARS), lines = head?.split("\n");
    const truncated = recordedTruncation || (recorded?.length ?? 0) > MAX_DIFF_CHARS || (lines?.length ?? 0) > MAX_DIFF_LINES;
    const diff = lines?.slice(0, MAX_DIFF_LINES).join("\n");
    const edit = { path: entry.path, position, additions: count(entry.additions), deletions: count(entry.deletions), truncated, ...(diff === undefined ? {} : { diff }) };
    edits.push({ ...edit, signature: String(hash32(JSON.stringify(edit))) });
  });
  evidenceCache.set(msg, edits);
  return edits;
}

/** Applied edits recorded in this conversation, per file, oldest edit first.
 * Evidence only: diff cards resolved "applied". Pending gates stay with their cards and a
 * discarded or refused edit (recorded "discarded") never appears. */
export function conversationChanges(messages: ChatMsg[]): ConversationChanges {
  const files = new Map<string, ConversationChange>();
  const signatures = new Map<string, string[]>();
  let turn = 0, card = 0;
  messages.forEach(msg => {
    if (msg.role === "user") { turn++; card = 0; return; }
    if (msg.type !== "diff_card" || msg.metadata?.resolved !== "applied") return;
    // Transport ids appear only after persistence. Turn/card ordinals survive that
    // reconciliation, including unrelated tool/progress messages being added/removed.
    const ordinal = card++;
    evidenceOf(msg).forEach(({ path, position, signature, ...evidence }) => {
      const change = files.get(path) ?? { path, additions: 0, deletions: 0, edits: [], signature: "" };
      const key = `${turn}:${ordinal}:${position}`;
      const edit = { ...evidence, signature, key, turn };
      change.edits.push(edit); change.additions += edit.additions; change.deletions += edit.deletions;
      files.set(path, change);
      const parts = signatures.get(path) ?? [];
      parts.push(`${key}:${signature}`); signatures.set(path, parts);
    });
  });
  // Fold short cached signatures, never all historical diff bytes on every tool event.
  const all = [...files.values()].map(change => ({ ...change, signature: `${change.edits.length}:${hash32(signatures.get(change.path)!.join(";"))}` }))
    .sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
  return { files: all.slice(0, CHANGES_FILE_LIMIT), omitted: Math.max(0, all.length - CHANGES_FILE_LIMIT) };
}

/** Resolve only a row from this exact active immutable transcript. No id/path fallback.
 * The weak evidence cache is shared with conversationChanges; only explicit clicks scan ordinals. */
export interface RecordedEditTarget { path: string; key: string; signature: string; editsInTurn: number; lastInTurn: boolean }
export function recordedEditTarget(messages: ChatMsg[], source: RecordedEditSource, path: string): RecordedEditTarget | undefined {
  const index = messages.indexOf(source.message), msg = source.message;
  if (index < 0 || !Number.isSafeInteger(source.position) || source.position < 0
    || msg.role !== "agent" || msg.type !== "diff_card" || msg.metadata?.resolved !== "applied") return;
  const evidence = evidenceOf(msg).find(edit => edit.position === source.position && edit.path === path);
  if (!evidence) return;
  let turn = 0, card = 0, start = 0;
  for (let i = 0; i < index; i++) {
    const previous = messages[i];
    if (previous.role === "user") { turn++; card = 0; start = i + 1; }
    else if (previous.type === "diff_card" && previous.metadata?.resolved === "applied") card++;
  }
  let editsInTurn = 0, lastMessage: ChatMsg | undefined, lastPosition = -1;
  for (let i = start; i < messages.length && messages[i].role !== "user"; i++) {
    const candidate = messages[i];
    if (candidate.type !== "diff_card" || candidate.metadata?.resolved !== "applied") continue;
    for (const edit of evidenceOf(candidate)) if (edit.path === path) {
      editsInTurn++; lastMessage = candidate; lastPosition = edit.position;
    }
  }
  return { path, key: `${turn}:${card}:${source.position}`, signature: evidence.signature, editsInTurn,
    lastInTurn: lastMessage === msg && lastPosition === source.position };
}
