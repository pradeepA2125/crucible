export interface DraftImage { name: string; mediaType: string; dataUrl: string; bytes: number; }
export interface Draft { text: string; imageAttachments: DraftImage[]; mentionedPaths: string[]; }
export type DraftUpdate = Omit<Draft, "imageAttachments"> & { imageAttachments?: DraftImage[] };
export interface ReturnedDraft { id: string; draft: Draft; }
export interface DraftSnapshot { threadId: string; revision: number; version: number; draft: Draft; returned?: ReturnedDraft; notice?: string; }
export const emptyDraft = (): Draft => ({ text: "", imageAttachments: [], mentionedPaths: [] });
