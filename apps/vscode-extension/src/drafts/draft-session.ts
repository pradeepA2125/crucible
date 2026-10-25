import { DraftStore } from "./draft-store.js";
import { DraftFlusher } from "./draft-flush.js";
import { draftSchemas } from "./protocol.js";
import type { Draft, DraftSnapshot, DraftUpdate } from "./types.js";
import type { ChatAdmissionView } from "../controller.js";
interface Ports {
  post(message: Record<string, unknown>): void;
  admission(value: ChatAdmissionView | null): void;
  identity(): unknown;
  changed?(urgent: boolean): void;
}
/** One project host owns drafts across view mounts. No disk storage or turn launch. */
export class DraftSession {
  private readonly store = new DraftStore();
  private readonly flusher = new DraftFlusher();
  private enabled = false;
  private navigating = false;
  private selected = "new";
  private inputEnabled = true;
  private turnActive = false;
  private turnKind: unknown = null;
  private historyBlocked = false;
  private safeNavigation = false;
  private controllerAdmission: ChatAdmissionView | null = null;
  private preparedOrigin: string | undefined;
  constructor(private readonly ports: Ports) {}
  matchesFeedback(key: string, revision: number, version: number, text?: string): boolean { return this.store.matchesFeedback(key, revision, version, text); }
  acceptFeedback(key: string, revision: number, version: number): void { const snapshot = this.store.acceptFeedback(key, revision, version); if (snapshot) this.changed(snapshot); }
  get hasSubmission(): boolean { return this.store.hasSubmission || !!this.controllerAdmission || this.navigating; }
  get hasRecovery(): boolean { return this.store.hasRecovery; }
  recover(): void { const value=this.store.recover(); if(value)this.changed(value); }
  exportState(): unknown { return this.store.exportState(); }
  importState(value: unknown): void { this.store.importState(value); }

  /** Observe the same retained host projection that the renderer receives. */
  observe(message: Record<string, unknown>): void {
    if (message.type === "clearThread" && typeof message.threadId === "string") { this.selected = message.threadId || "new"; this.turnKind = null; this.turnActive = false; }
    if (message.type === "renderThreadList" && typeof message.activeThreadId === "string") this.selected = message.activeThreadId || "new";
    if (message.type === "setInputEnabled") this.inputEnabled = message.enabled === true;
    if (message.type === "liveStatus") { this.turnActive = message.turnActive === true; this.turnKind = message.turnKind; }
    if (message.type === "historyState") {
      const history = message.history as { threadId?: string; phase?: string; navigationSafe?: boolean } | undefined;
      if (history?.threadId === this.selected) { this.historyBlocked = history.phase !== "current"; this.safeNavigation = history.navigationSafe === true; }
    }
  }
  renderAdmission(value: ChatAdmissionView | null): void { this.controllerAdmission = value; this.publish(); }
  private publish(): void {
    this.ports.admission(this.controllerAdmission ?? (this.store.hasSubmission
      ? { threadId: this.preparedOrigin ?? this.selected, state: "pending", checking: false } : null));
  }
  private changed(snapshot?: DraftSnapshot): void {
    this.ports.changed?.(false);
    if (snapshot) this.ports.post({ type: "draft/changed", snapshot });
    if (!this.store.hasSubmission) this.preparedOrigin = undefined;
    this.publish();
  }
  reject(id: string): DraftSnapshot | undefined { const snapshot = this.store.reject(id); if (snapshot) this.changed(snapshot); return snapshot; }
  rejectPrepared(id: string): void { if (this.store.isPrepared(id)) this.reject(id); }
  abandonPrepared(): void { const snapshot = this.store.abandonPrepared(); if (snapshot) this.changed(snapshot); }
  created(id: string, threadId: string): void { this.store.move(id, threadId); if (this.preparedOrigin === "new") this.preparedOrigin = threadId; this.publish(); }
  accepted(id: string, threadId: string): void { const snapshot = this.store.accept(id, threadId); if (snapshot) this.changed(snapshot); }
  resetView(): void { this.enabled = false; this.abandonPrepared(); }
  /** Composer IDs consume only their own preparation. Legacy card feedback has none. */
  dispatch(message: Record<string, unknown>): boolean {
    if (message.type !== "sendMessage") return false;
    if (this.navigating) throw new Error("Wait for conversation navigation to finish before sending.");
    if (typeof message.draftId !== "string") {
      if (this.store.hasSubmission || this.controllerAdmission) throw new Error("Finish the pending composer send before sending card feedback.");
      return false;
    }
    if (!this.enabled || !this.store.dispatch(message.draftId)) throw new Error("Prepare the current draft before sending. A duplicate send was refused.");
    return true;
  }
  async send<T>(draftId: string | undefined, action: () => Promise<T>): Promise<T> {
    try { return await action(); }
    catch (error) { if (draftId) this.reject(draftId); throw error; }
  }
  private assertIdle(): void {
    if (this.store.hasSubmission || this.controllerAdmission || !this.inputEnabled || this.turnActive)
      throw new Error("Finish the current turn or pending send decision before changing conversations.");
  }
  async navigate(action: () => Promise<void>, flush = true): Promise<void> {
    if (this.navigating) throw new Error("Conversation navigation is already in progress.");
    if (!this.safeNavigation) this.assertIdle();
    if (this.store.hasSubmission || this.controllerAdmission) throw new Error("Finish the pending send decision before changing conversations.");
    this.navigating = true;
    const identity = this.ports.identity();
    try {
      if (flush && this.enabled) await this.flusher.request(identity, message => this.ports.post(message));
      if (identity !== this.ports.identity()) throw new Error("The conversation view changed. Retry navigation in the current view.");
      if (!this.safeNavigation) this.assertIdle();
      if (this.store.hasSubmission || this.controllerAdmission) throw new Error("A send decision began while saving. Retry navigation.");
      await action();
    } finally { this.navigating = false; }
  }
  /** Draft replies are correlated even on validation failure; never enter chat error recovery. */
  handle(message: Record<string, unknown>, identity = this.ports.identity()): boolean {
    if (typeof message.type !== "string" || !message.type.startsWith("draft/")) return false;
    try {
      const schema = draftSchemas.get(message.type);
      if (!schema) throw new Error("Unknown draft request.");
      const payload = schema.parse(message) as Record<string, unknown>;
      if (identity !== this.ports.identity()) throw new Error("Draft view is no longer current.");
      if (Buffer.byteLength(JSON.stringify(payload), "utf8") > 48 * 1024 * 1024) throw new Error("Draft request exceeds the message size limit.");
      if (payload.type === "draft/flushAck") { this.flusher.acknowledge(identity, payload.requestId as string, payload.error as string | undefined); return true; }
      const threadId = payload.threadId as string;
      let snapshot: DraftSnapshot | undefined;
      switch (payload.type) {
        case "draft/read": this.enabled = true; snapshot = this.store.read(threadId); break;
        case "draft/save": snapshot = this.store.save(threadId, payload.revision as number, payload.draft as DraftUpdate); break;
        case "draft/begin":
          if (this.store.hasSubmission || this.controllerAdmission || !this.inputEnabled || this.turnActive && this.turnKind !== "notice")
            throw new Error("Finish the current turn or pending send decision before sending.");
          if (this.historyBlocked) throw new Error("Wait for saved history to reconnect before sending.");
          if (this.navigating || threadId !== this.selected) throw new Error("The selected conversation changed. Retry sending its current draft.");
          snapshot = this.store.begin(threadId, payload.revision as number, payload.draft as Draft, payload.draftId as string);
          this.preparedOrigin = threadId;
          try { this.ports.changed?.(true); }
          catch (error) { this.store.reject(payload.draftId as string); this.preparedOrigin=undefined; this.publish(); throw error; }
          this.publish(); break;
        case "draft/choose": snapshot = this.store.choose(threadId, payload.revision as number, payload.version as number, payload.draft as Draft, payload.choice as "restore" | "discard"); break;
        case "draft/reject": snapshot = this.store.isPrepared(payload.draftId as string) ? this.reject(payload.draftId as string) : undefined; break;
      }
      if (snapshot && payload.type !== "draft/read" && payload.type !== "draft/begin") this.ports.changed?.(false);
      if (snapshot && payload.type === "draft/save" && snapshot.revision === payload.revision && snapshot.version === payload.version) {
        const { imageAttachments: _images, ...draft } = snapshot.draft;
        const { returned: _returned, ...metadata } = snapshot;
        this.ports.post({ type: "draft/result", requestId: payload.requestId, snapshot: { ...metadata, draft, imagesUnchanged: true, returnedUnchanged: true } });
      } else this.ports.post({ type: "draft/result", requestId: payload.requestId, snapshot });
    } catch (error) {
      if (typeof message.requestId === "string" && message.requestId.length <= 1024 && identity === this.ports.identity())
        this.ports.post({ type: "draft/result", requestId: message.requestId, error: error instanceof Error ? error.message : "Draft request failed." });
    }
    return true;
  }
  dispose(): void { this.store.clear(); this.controllerAdmission = null; this.preparedOrigin = undefined; this.enabled = false; }
}
