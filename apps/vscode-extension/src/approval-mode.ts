import { approvalFromWire, type ApprovalSnapshot, type ApprovalState, type BackendTaskClient } from "@crucible/editor-client";

export type ApprovalMode = "ask" | "accept_edits" | "full_access";
export interface ApprovalView {
  tier: "unknown" | "supported" | "older" | "legacy";
  snapshot: ApprovalSnapshot | null;
  mode: ApprovalMode | null;
  confirmed: boolean;
  pending: boolean;
  warningDismissed: boolean;
  error: string | null;
  notice: string | null;
}
export interface ApprovalDependencies {
  client(): BackendTaskClient;
  endpoint?(): string;
  savedMode(): ApprovalMode | undefined;
  reviewOn(): boolean;
  saveMode(mode: ApprovalMode): Promise<void>;
  warningDismissed(): boolean;
  saveWarningDismissed(): Promise<void>;
  render(view: ApprovalView): void;
}
export function isApprovalMode(value: unknown): value is ApprovalMode {
  return value === "ask" || value === "accept_edits" || value === "full_access";
}
export function policyFor(mode: ApprovalMode): { autoAccept: boolean; shellPolicy: "ask" | "allow_all" } {
  return { autoAccept: mode !== "ask", shellPolicy: mode === "full_access" ? "allow_all" : "ask" };
}
export function modeFor(state: ApprovalSnapshot): ApprovalMode | null {
  if (!state.autoAccept) return state.shellPolicy === "ask" ? "ask" : null;
  return state.shellPolicy === "allow_all" ? "full_access" : "accept_edits";
}
export function migrateApproval(shell: "ask" | "allow_all", reviewOn: boolean): ApprovalMode {
  return reviewOn ? "ask" : shell === "allow_all" ? "full_access" : "accept_edits";
}
export function isApprovalAdmissionError(error: unknown): boolean {
  const e = error as { status?: number; detail?: { code?: string } } | null;
  return e?.status === 409 && e.detail?.code === "approval_not_confirmed";
}

/** One queue owns selection and synchronization writes. Readback, never local preference,
 * drives the supported tier. Restart recovery initializes with resolveWaiting=false. */
export class ApprovalCoordinator {
  private queue: Promise<unknown> = Promise.resolve();
  private connecting: Promise<void> | null = null;
  private view: ApprovalView;
  private endpoint: string | undefined;
  private observedProcessId: string | undefined;
  constructor(private readonly deps: ApprovalDependencies) {
    this.view = { tier: "unknown", snapshot: null, mode: null, confirmed: false, pending: false,
      warningDismissed: deps.warningDismissed(), error: null, notice: null };
  }
  current(): ApprovalView { return this.view; }
  private publish(patch: Partial<ApprovalView>): void {
    this.view = { ...this.view, ...patch, warningDismissed: this.deps.warningDismissed() };
    this.deps.render(this.view);
  }
  private enqueue<T>(work: () => Promise<T>): Promise<T> {
    const next = this.queue.catch(() => undefined).then(work);
    this.queue = next;
    return next;
  }
  private adopt(snapshot: ApprovalSnapshot): void {
    this.observedProcessId = snapshot.processId;
    if (this.view.snapshot?.processId === snapshot.processId && this.view.snapshot.version > snapshot.version) return;
    this.publish({ tier: "supported", snapshot, mode: modeFor(snapshot), confirmed: snapshot.owned, error: null });
  }
  private conflict(error: unknown): ApprovalSnapshot | null {
    const e = error as { status?: number; detail?: unknown };
    if (e?.status !== 409) return null;
    try { const state = approvalFromWire(e.detail); return state?.supported ? state : null; } catch { return null; }
  }
  private async persist(mode: ApprovalMode): Promise<void> {
    try { await this.deps.saveMode(mode); }
    catch (error) { this.publish({ error: `Applied, but the preference could not be saved: ${error instanceof Error ? error.message : String(error)}` }); }
  }
  private endpointChanged(): boolean {
    return this.deps.endpoint !== undefined && this.endpoint !== this.deps.endpoint();
  }
  private async connectNow(): Promise<void> {
    this.publish({ pending: true, error: null });
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        const endpoint = this.deps.endpoint?.();
        if (endpoint !== this.endpoint) this.publish({ tier: "unknown", snapshot: null, mode: null, confirmed: false });
        this.endpoint = endpoint;
        const client = this.deps.client();
        const config = await client.getConfig();
        if (this.endpointChanged()) continue;
        const state = config.approval;
        if (!state?.supported) {
          if (!config.chatControllerEnabled) {
            this.publish({ tier: "legacy", snapshot: null, mode: null, confirmed: true, pending: false });
            return;
          }
          const mode = this.deps.savedMode() ?? (this.deps.reviewOn() ? "ask" : "accept_edits");
          if (mode === "ask") {
            await client.setGlobalReviewPref({ autoAccept: false });
            this.publish({ tier: "older", snapshot: null, mode, confirmed: true, pending: false });
          } else {
            this.publish({ tier: "older", snapshot: null, mode: mode === "full_access" ? "accept_edits" : mode,
              confirmed: false, pending: false });
          }
          return;
        }
        if (state.owned) {
          this.adopt(state);
          if (!this.deps.savedMode() && modeFor(state)) await this.persist(modeFor(state)!);
          this.publish({ pending: false });
          return;
        }
        const saved = this.deps.savedMode();
        const mode = saved ?? migrateApproval(state.shellPolicy, this.deps.reviewOn());
        try {
          const applied = await client.setApprovalMode({ ...policyFor(mode), resolveWaiting: false,
            expectedProcessId: state.processId, expectedVersion: state.version });
          if (this.endpointChanged()) continue;
          this.adopt(applied);
          await this.persist(mode);
          this.publish({ pending: false, notice: !saved && state.shellPolicy === "allow_all" && this.deps.reviewOn()
            ? "Commands now ask for approval. You can change this in approval modes." : null });
          return;
        } catch (error) {
          const current = this.conflict(error);
          if (!current) throw error;
          if (current.owned) { this.adopt(current); this.publish({ pending: false }); return; }
          // Another restart/unowned conflict: read config again, never resolve edits.
        }
      }
      throw new Error("Approval mode not confirmed — retry");
    } catch (error) {
      this.publish({ pending: false, confirmed: false, error: `Not applied — retry: ${error instanceof Error ? error.message : String(error)}` });
      throw error;
    }
  }
  connect(): Promise<void> {
    if (this.connecting) return this.connecting;
    const work = this.enqueue(() => this.connectNow());
    this.connecting = work;
    void work.finally(() => { if (this.connecting === work) this.connecting = null; }).catch(() => undefined);
    return work;
  }
  async ready(timeoutMs = 10000): Promise<string | undefined> {
    if (this.endpointChanged()) this.publish({ tier: "unknown", snapshot: null, mode: null, confirmed: false });
    if (this.view.tier !== "unknown" && (this.view.tier !== "supported" || this.view.confirmed)) {
      return this.view.tier === "supported" ? this.view.snapshot?.processId : undefined;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([this.connect(), new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("Approval mode not confirmed — retry")), timeoutMs);
      })]);
      if (this.view.tier === "supported" && !this.view.confirmed) throw new Error("Approval mode not confirmed — retry");
      return this.view.tier === "supported" ? this.view.snapshot?.processId : undefined;
    } finally { if (timer) clearTimeout(timer); }
  }
  observe(state: ApprovalState | undefined, processId?: string | null): void {
    const changedProcess = !!processId && !!this.observedProcessId && processId !== this.observedProcessId;
    if (processId) this.observedProcessId = processId;
    if (!state?.supported) {
      if (changedProcess || state?.supported === false && this.view.tier === "supported") {
        this.publish({ tier: "unknown", snapshot: null, mode: null, confirmed: false, pending: true });
        void this.connect().catch(() => undefined);
      }
      return;
    }
    const snapshot = state;
    if (!snapshot.owned || this.view.snapshot?.processId !== snapshot.processId || this.view.tier !== "supported") {
      this.publish({ tier: "supported", confirmed: false, pending: true });
      void this.connect().catch(() => undefined);
      return;
    }
    this.adopt(snapshot);
  }
  async recover(error: unknown): Promise<boolean> {
    if (!isApprovalAdmissionError(error)) return false;
    this.publish({ confirmed: false, pending: true });
    await this.connect().catch(() => undefined);
    this.publish({ notice: "The backend restarted. Your input and unanswered cards are kept — retry." });
    return true;
  }
  select(mode: ApprovalMode, fullAccessConfirmed = false, dontShowAgain = false): Promise<void> {
    return this.enqueue(async () => {
      this.publish({ pending: true, error: null, notice: null });
      try {
        if (this.endpointChanged()) this.publish({ tier: "unknown", snapshot: null, mode: null, confirmed: false });
        if (this.view.tier === "unknown" || (this.view.tier === "supported" && !this.view.confirmed)) await this.connectNow();
        if (this.view.tier === "legacy") throw new Error("Approval modes need the chat controller");
        if (mode === "full_access" && !this.deps.warningDismissed() && !fullAccessConfirmed) throw new Error("Confirm Full access first");
        const client = this.deps.client();
        if (this.view.tier === "older") {
          if (mode === "full_access") throw new Error("Full access requires an updated backend");
          await client.setGlobalReviewPref({ autoAccept: mode === "accept_edits" });
          await this.persist(mode);
          this.publish({ mode, confirmed: true, pending: false });
          return;
        }
        const before = this.view.snapshot!;
        try {
          const applied = await client.setApprovalMode({ ...policyFor(mode), resolveWaiting: true,
            expectedProcessId: before.processId, expectedVersion: before.version });
          this.adopt(applied);
          await this.persist(mode);
          if (mode === "full_access" && dontShowAgain) {
            try { await this.deps.saveWarningDismissed(); }
            catch { this.publish({ error: "Applied, but the Full access reminder could not be saved." }); }
          }
          this.publish({ pending: false });
        } catch (error) {
          const current = this.conflict(error);
          if (!current) throw error;
          if (current.processId !== before.processId) {
            this.publish({ confirmed: false });
            await this.connectNow();
            this.publish({ pending: false, notice: "The backend restarted. Pick the mode again to apply it." });
          } else {
            this.adopt(current);
            this.publish({ pending: false, notice: "Approval mode was changed elsewhere. The current backend mode is shown." });
          }
        }
      } catch (error) {
        this.publish({ pending: false, error: `Not applied — retry: ${error instanceof Error ? error.message : String(error)}` });
      }
    });
  }
}
