export interface DraftImage { name: string; mediaType: string; dataUrl: string; bytes: number; }
export interface Draft { text: string; imageAttachments: DraftImage[]; mentionedPaths: string[]; }
export type DraftUpdate = Omit<Draft, "imageAttachments"> & { imageAttachments?: DraftImage[] };
export interface ReturnedDraft { id: string; draft: Draft; }
export interface DraftSnapshot { threadId: string; revision: number; version: number; draft: Draft; returned?: ReturnedDraft; notice?: string; }
export interface DraftPort {
  flush?(): Promise<void>;
  read(threadId: string): Promise<DraftSnapshot>;
  save(threadId: string, revision: number, draft: Draft): Promise<DraftSnapshot>;
  begin(threadId: string, revision: number, draft: Draft, draftId: string): Promise<DraftSnapshot>;
  choose(threadId: string, revision: number, version: number, current: Draft, choice: "restore" | "discard"): Promise<DraftSnapshot>;
  onChange(listener: (snapshot: DraftSnapshot) => void): () => void;
  onAuxiliaryFlush?(flush: () => Promise<void>): () => void;
  onFlush(flush: () => Promise<void>): () => void;
}
export const emptyDraft = (): Draft => ({ text: "", imageAttachments: [], mentionedPaths: [] });
