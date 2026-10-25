import type { DraftImage, DraftPort, DraftSnapshot, DraftUpdate } from "./types";
type WireSnapshot = Omit<DraftSnapshot, "draft"> & { draft: DraftUpdate; imagesUnchanged?: boolean; returnedUnchanged?: boolean };
/** Shared async draft adapter. Keystrokes omit unchanged image data in both directions. */
export function createDraftClient(send: (message: unknown) => void, target: Window = window, beforeFlush?: () => Promise<void>): DraftPort & { dispose(): void } {
  const cache = new Map<string, DraftSnapshot>();
  const sizes = new Map<string, number>();
  const imageSizes = new WeakMap<DraftImage[], number>();
  let cacheBytes = 0;
  const bytes = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).byteLength;
  const imageBytes = (images: DraftImage[]) => {
    let size = imageSizes.get(images);
    if (size === undefined) { size = bytes(images); imageSizes.set(images, size); }
    return size;
  };
  const retain = (snapshot: DraftSnapshot) => {
    const metadata = { ...snapshot, draft: { ...snapshot.draft, imageAttachments: [] },
      ...(snapshot.returned ? { returned: { ...snapshot.returned, draft: { ...snapshot.returned.draft, imageAttachments: [] } } } : {}) };
    const size = bytes(metadata) + imageBytes(snapshot.draft.imageAttachments) - 2
      + (snapshot.returned ? imageBytes(snapshot.returned.draft.imageAttachments) - 2 : 0);
    cacheBytes += size - (sizes.get(snapshot.threadId) ?? 0);
    cache.delete(snapshot.threadId); cache.set(snapshot.threadId, snapshot); sizes.set(snapshot.threadId, size);
    while (cache.size > 32 || cacheBytes > 64 * 1024 * 1024) {
      const oldest = cache.keys().next().value!;
      cacheBytes -= sizes.get(oldest) ?? 0; cache.delete(oldest); sizes.delete(oldest);
    }
  };
  const requests = new Map<string, { finish(snapshot?: WireSnapshot, error?: string): void }>();
  const listeners = new Set<(snapshot: DraftSnapshot) => void>();
  let flush: (() => Promise<void>) | undefined;
  const auxiliary = new Set<() => Promise<void>>();
  const flushAll = async () => { await flush?.(); for (const save of [...auxiliary]) await save(); };
  let generation = 0;
  const decode = (wire: WireSnapshot, supplied?: DraftImage[]): DraftSnapshot => {
    const previous = cache.get(wire.threadId);
    const snapshot: DraftSnapshot = { threadId: wire.threadId, revision: wire.revision, version: wire.version,
      draft: { ...wire.draft, imageAttachments: wire.draft.imageAttachments ?? supplied ?? previous?.draft.imageAttachments ?? [] },
      ...(wire.returnedUnchanged ? previous?.returned ? { returned: previous.returned } : {} : wire.returned ? { returned: wire.returned } : {}),
      ...(wire.notice ? { notice: wire.notice } : {}) };
    if (!previous || (snapshot.version >= previous.version && snapshot.revision >= previous.revision)) retain(snapshot);
    return snapshot;
  };
  const notify = (snapshot?: DraftSnapshot) => { if (snapshot) for (const listener of listeners) listener(snapshot); };
  const request = (type: string, fields: Record<string, unknown>, images?: DraftImage[]): Promise<DraftSnapshot | undefined> => {
    const requestId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { requests.delete(requestId); reject(new Error("Draft could not be saved. Retry after the conversation reconnects.")); }, 4000);
      requests.set(requestId, { finish: (wire, error) => { clearTimeout(timer); requests.delete(requestId); if (error) reject(new Error(error)); else resolve(wire ? decode(wire, images) : undefined); } });
      send({ type, requestId, ...fields });
    });
  };
  const snapshot = async (type: string, fields: Record<string, unknown>, images?: DraftImage[]) => {
    const result = await request(type, fields, images); if (!result) throw new Error("Draft response was unavailable."); return result;
  };
  const report = (error: unknown) => target.postMessage({ type: "appendMessage", message: { role: "agent", type: "notice", content: error instanceof Error ? error.message : "Draft recovery failed.", timestamp: new Date().toISOString(), metadata: {} } }, "*");
  const onMessage = (event: MessageEvent) => {
    const message = event.data;
    if (message?.type === "draft/result") requests.get(message.requestId)?.finish(message.snapshot, message.error);
    else if (message?.type === "draft/changed") notify(decode(message.snapshot));
    else if (message?.type === "draftRecovery" && typeof message.draftId === "string") {
      void request("draft/reject", { draftId: message.draftId }).then(notify, report);
    } else if (message?.type === "draft/flush") {
      void (async () => {
        try { if (beforeFlush) await beforeFlush(); await flushAll(); send({ type: "draft/flushAck", requestId: message.requestId }); }
        catch (error) { send({ type: "draft/flushAck", requestId: message.requestId, error: error instanceof Error ? error.message : "Draft save failed." }); }
      })();
    }
  };
  target.addEventListener("message", onMessage);
  return {
    flush: async () => { if (beforeFlush) await beforeFlush(); await flushAll(); },
    read: threadId => snapshot("draft/read", { threadId }),
    save: async (threadId, revision, draft) => {
      const previous = cache.get(threadId);
      const sameImages = previous?.draft.imageAttachments.length === draft.imageAttachments.length
        && draft.imageAttachments.every((image, index) => image.dataUrl === previous.draft.imageAttachments[index]?.dataUrl);
      const update = { text: draft.text, mentionedPaths: draft.mentionedPaths, ...(!sameImages ? { imageAttachments: draft.imageAttachments } : {}) };
      const result = await snapshot("draft/save", { threadId, revision, version: previous?.version ?? 0, draft: update }, draft.imageAttachments);
      if (result.version > (previous?.version ?? 0)) notify(result);
      return result;
    },
    begin: async (threadId, revision, draft, draftId) => {
      const origin = generation;
      let result: DraftSnapshot;
      try { result = await snapshot("draft/begin", { threadId, revision, draft, draftId }); }
      catch (error) { await request("draft/reject", { draftId }).catch(report); throw error; }
      if (origin !== generation) { await request("draft/reject", { draftId }); throw new Error("The composer changed; the unsent message was returned to its conversation."); }
      return result;
    },
    choose: (threadId, revision, version, draft, choice) => snapshot("draft/choose", { threadId, revision, version, draft, choice }),
    onChange: listener => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    onAuxiliaryFlush: callback => { auxiliary.add(callback); return () => { auxiliary.delete(callback); }; },
    onFlush: callback => { flush = callback; return () => { if (flush === callback) flush = undefined; ++generation; }; },
    dispose: () => { target.removeEventListener("message", onMessage); for (const entry of [...requests.values()]) entry.finish(undefined, "Draft view closed."); listeners.clear(); auxiliary.clear(); flush = undefined; cache.clear(); sizes.clear(); cacheBytes = 0; ++generation; },
  };
}
