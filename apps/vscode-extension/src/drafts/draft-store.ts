import { savedDrafts } from "./protocol.js";
import { emptyDraft, type Draft, type DraftUpdate, type DraftSnapshot } from "./types.js";
export type { Draft } from "./types.js";
interface Submission { origin: string; id: string; draft: Draft; dispatched: boolean; }
/** Project-owned memory only. Current/returned entries cap64MiB; one pinned
 * submission has its own48MiB reservation and is never hydrated or evicted. */
export class DraftStore {
  private entries = new Map<string, DraftSnapshot>();
  private sizes = new Map<string, number>();
  private images = new WeakMap<Draft["imageAttachments"], number>();
  private total = 0;
  private tombstones = new Map<string, { revision: number; version: number; notice?: string }>();
  private submission: Submission | undefined;
  private recovery: {origin:string;id:string;draft:Draft} | undefined;
  constructor(private readonly budget = 64 * 1024 * 1024, private readonly count = 32) {}
  get hasSubmission(): boolean { return !!this.submission; }
  read(threadId: string): DraftSnapshot {
    const entry = this.entries.get(threadId);
    if (entry) {
      const delivered = { ...entry };
      if (entry.notice) { const clean = { ...entry }; delete clean.notice; this.put(clean); }
      const retained = this.entries.get(threadId)!; this.entries.delete(threadId); this.entries.set(threadId, retained); return delivered;
    }
    const grave = this.tombstones.get(threadId);
    const result = { threadId, revision: grave?.revision ?? 0, version: grave?.version ?? 0, draft: emptyDraft(), ...(grave?.notice ? { notice: grave.notice } : {}) };
    if (grave) delete grave.notice;
    return result;
  }
  save(threadId: string, revision: number, update: DraftUpdate): DraftSnapshot {
    const previous = this.read(threadId);
    if (revision <= previous.revision) return previous;
    const draft = { ...update, imageAttachments: update.imageAttachments ?? previous.draft.imageAttachments };
    const entry = { ...previous, revision, draft }; delete entry.notice;
    this.put(entry); this.tombstones.delete(threadId); this.trim(threadId); return this.read(threadId);
  }
  begin(threadId: string, revision: number, draft: Draft, id: string): DraftSnapshot {
    if(this.recovery)throw new Error("This project has an interrupted send in quarantine. Review saved history and recover it in desktop Settings before sending again.");
    if (this.submission) throw new Error("A message is already being admitted. Wait for its decision before sending again.");
    const previous = this.read(threadId);
    if (previous.returned) throw new Error("Restore or discard the returned message before sending another.");
    if (revision <= previous.revision) throw new Error("This draft changed. Retry sending the current draft.");
    const encoded = Buffer.byteLength(JSON.stringify({ ...draft, imageAttachments: [] })) + this.imageBytes(draft.imageAttachments) - 2;
    if (encoded > 48 * 1024 * 1024) throw new Error("The pending draft exceeds the message size limit.");
    this.submission = { origin: threadId, id, draft, dispatched: false };
    this.put({ ...previous, revision, draft: emptyDraft() }); this.trim(threadId); return this.read(threadId);
  }
  isPrepared(id: string): boolean { return this.submission?.id === id && !this.submission.dispatched; }
  dispatch(id: string): boolean {
    if (this.submission?.id !== id || this.submission.dispatched) return false;
    this.submission.dispatched = true; return true;
  }
  abandonPrepared(): DraftSnapshot | undefined { return this.submission && !this.submission.dispatched ? this.reject(this.submission.id) : undefined; }
  move(id: string, threadId: string): void {
    if (this.submission?.id !== id || this.submission.origin !== "new") return;
    const previous = this.entries.get("new"); this.remove("new");
    if (previous && !this.entries.has(threadId)) this.put({ ...previous, threadId });
    this.submission.origin = threadId;
  }
  accept(id: string, threadId: string): DraftSnapshot | undefined {
    if (this.submission?.id !== id) return undefined;
    this.move(id, threadId); const origin = this.submission.origin; this.submission = undefined;
    const entry = this.read(origin); this.put({ ...entry, version: entry.version + 1 }); return this.read(origin);
  }
  reject(id: string): DraftSnapshot | undefined {
    if (this.submission?.id !== id) return undefined;
    const pending = this.submission; this.submission = undefined;
    const previous = this.read(pending.origin);
    this.put({ ...previous, version: previous.version + 1, returned: { id, draft: pending.draft } });
    this.trim(pending.origin); return this.read(pending.origin);
  }
  choose(threadId: string, revision: number, version: number, current: Draft, choice: "restore" | "discard"): DraftSnapshot {
    const previous = this.read(threadId);
    if (version !== previous.version || revision <= previous.revision || !previous.returned) throw new Error("The returned draft changed. Retry the choice after the conversation updates.");
    const entry: DraftSnapshot = { ...previous, revision, version: previous.version + 1, draft: choice === "restore" ? previous.returned.draft : current };
    if (choice === "restore" && (current.text || current.imageAttachments.length)) entry.returned = { id: `swap:${entry.version}`, draft: current };
    else delete entry.returned;
    this.put(entry); this.trim(threadId); return this.read(threadId);
  }
  matchesFeedback(key: string, revision: number, version: number, text?: string): boolean {
    const entry = this.read(key);
    return key.startsWith("@gate:") && entry.revision === revision && entry.version === version
      && (text === undefined || entry.draft.text === text);
  }
  acceptFeedback(key: string, revision: number, version: number): DraftSnapshot | undefined {
    if (!this.matchesFeedback(key, revision, version)) return undefined;
    const previous = this.read(key);
    this.put({ ...previous, revision: revision + 1, version: version + 1, draft: emptyDraft() });
    return this.read(key);
  }
  exportState(): unknown {
    return { version:1, entries:[...this.entries.values()], ...((this.submission??this.recovery) ? {pending:{origin:(this.submission??this.recovery)!.origin,id:(this.submission??this.recovery)!.id,draft:(this.submission??this.recovery)!.draft}} : {}), tombstones:[...this.tombstones].map(([threadId,value]) => ({threadId,...value})) };
  }
  importState(raw: unknown): void {
    if (this.submission || this.entries.size) throw new Error("Restore drafts only into an empty project session.");
    if (Buffer.byteLength(JSON.stringify(raw)) > this.budget) throw new Error("Saved drafts exceed the memory limit.");
    const value = savedDrafts.parse(raw);
    const keys = [...value.entries.map(e=>e.threadId), ...value.tombstones.map(e=>e.threadId)];
    if (new Set(keys).size !== keys.length) throw new Error("Saved draft identities are duplicated.");
    // Validate the whole packet before mutating the live store; never trim silently.
    for (const e of value.entries) this.put({threadId:e.threadId,revision:e.revision,version:e.version,draft:e.draft,...(e.returned?{returned:e.returned}:{}),...(e.notice?{notice:e.notice}:{})});
    if(value.pending)this.recovery=value.pending;
    if (this.total > this.budget || this.entries.size > this.count) { this.clear(); throw new Error("Saved drafts exceed the memory limit."); }
    for (const {threadId,...grave} of value.tombstones) this.tombstones.set(threadId,{revision:grave.revision,version:grave.version,...(grave.notice?{notice:grave.notice}:{})});
  }
  get hasRecovery(): boolean { return !!this.recovery; }
  recover(): DraftSnapshot | undefined {
    if(!this.recovery)return;
    const r=this.recovery, previous=this.read(r.origin);
    if(previous.returned)throw new Error("Restore or discard the existing returned draft first.");
    this.recovery=undefined;
    this.put({...previous,version:previous.version+1,returned:{id:r.id,draft:r.draft},notice:"Recovered after explicit duplicate-risk confirmation. Review saved history before sending."});
    this.trim(r.origin);return this.read(r.origin);
  }
  clear(): void { this.entries.clear(); this.sizes.clear(); this.total = 0; this.tombstones.clear(); this.submission = undefined; this.recovery=undefined; }
  private imageBytes(images: Draft["imageAttachments"]): number {
    let size = this.images.get(images);
    if (size === undefined) { size = Buffer.byteLength(JSON.stringify(images)); this.images.set(images, size); }
    return size;
  }
  private put(entry: DraftSnapshot): void {
    this.tombstones.delete(entry.threadId);
    // Only text/metadata are serialized on keystrokes; image-array sizes are cached.
    const metadata = { ...entry, draft: { ...entry.draft, imageAttachments: [] }, ...(entry.returned ? { returned: { ...entry.returned, draft: { ...entry.returned.draft, imageAttachments: [] } } } : {}) };
    const size = Buffer.byteLength(JSON.stringify(metadata)) + this.imageBytes(entry.draft.imageAttachments) - 2
      + (entry.returned ? this.imageBytes(entry.returned.draft.imageAttachments) - 2 : 0);
    this.total += size - (this.sizes.get(entry.threadId) ?? 0); this.sizes.set(entry.threadId, size); this.entries.set(entry.threadId, entry);
  }
  private remove(key: string): void { this.total -= this.sizes.get(key) ?? 0; this.sizes.delete(key); this.entries.delete(key); }
  private trim(active: string): void {
    for (const [key, entry] of this.entries) {
      if (this.total <= this.budget) break;
      if (key === active) continue;
      if (entry.draft.imageAttachments.length || entry.returned?.draft.imageAttachments.length) {
        this.put({ ...entry, version: entry.version + 1, draft: { ...entry.draft, imageAttachments: [] },
          ...(entry.returned ? { returned: { ...entry.returned, draft: { ...entry.returned.draft, imageAttachments: [] } } } : {}), notice: "Draft text was kept; add images again because the draft memory limit was reached." });
      }
    }
    const entry = this.entries.get(active);
    if (this.total > this.budget && entry?.returned?.draft.imageAttachments.length) this.put({ ...entry, version: entry.version + 1,
      returned: { ...entry.returned, draft: { ...entry.returned.draft, imageAttachments: [] } }, notice: "Returned draft images were dropped to save memory; its text was kept." });
    while (this.entries.size > this.count || this.total > this.budget) {
      const oldest = [...this.entries.keys()].find(key => key !== active);
      if (oldest === undefined) break; // One legal current draft always fits64MiB.
      const old = this.entries.get(oldest)!; this.remove(oldest);
      this.tombstones.set(oldest, { revision: old.revision, version: old.version + 1, notice: "This draft exceeded the session memory limit and could not be retained." });
      if (this.tombstones.size > this.count) this.tombstones.delete(this.tombstones.keys().next().value!);
    }
  }
}
