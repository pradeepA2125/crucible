import { approvalFromWire, type ApprovalSnapshot, type ApprovalState, type BackendTaskClient, type ShellPolicy } from "@crucible/editor-client";

export type ApprovalMode = "ask" | "accept_edits" | "auto" | "full_access";
export interface ApprovalView {
  tier: "unknown" | "supported" | "older" | "legacy";
  snapshot: ApprovalSnapshot | null;
  mode: ApprovalMode | null;
  confirmed: boolean;
  pending: boolean;
  warningDismissed: boolean;
  /** Full access is only applied in a trusted workspace. */
  trusted: boolean;
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
  isTrusted(): boolean;
  /** Per-workspace: Full access was confirmed here, by a selection or the first-send dialog. */
  firstSendAcknowledged(): boolean;
  acknowledgeFirstSend(): Promise<void>;
  confirmFirstFullAccessSend(): Promise<FirstSendChoice>;
  /** The user's sandbox switch, global like the saved mode. */
  sandboxEnabled(): boolean;
  saveSandboxEnabled(enabled: boolean): Promise<void>;
  /** The sandbox is on but cannot run; commands are unprotected. Called once per window. */
  notifySandboxUnavailable(reason: string): void;
  render(view: ApprovalView): void;
}
export type FirstSendChoice = "continue" | "accept_edits" | "cancel";

export function isApprovalMode(value: unknown): value is ApprovalMode {
  return value === "ask" || value === "accept_edits" || value === "auto" || value === "full_access";
}
export function policyFor(mode: ApprovalMode): { autoAccept: boolean; shellPolicy: ShellPolicy } {
  const shellPolicy: ShellPolicy = mode === "full_access" ? "allow_all" : mode === "auto" ? "auto" : "ask";
  return { autoAccept: mode !== "ask", shellPolicy };
}
export function modeFor(state: ApprovalSnapshot): ApprovalMode | null {
  if (!state.autoAccept) return state.shellPolicy === "ask" ? "ask" : null;
  if (state.shellPolicy === "allow_all") return "full_access";
  return state.shellPolicy === "auto" ? "auto" : "accept_edits";
}
/** Auto and Full access run commands without asking first, so both need a trusted workspace. */
function needsTrust(mode: ApprovalMode): boolean {
  return mode === "full_access" || mode === "auto";
}
function offersAuto(state: ApprovalSnapshot | null | undefined): boolean {
  return state?.shellPolicies?.includes("auto") ?? false;
}
export function migrateApproval(shell: ShellPolicy, reviewOn: boolean): ApprovalMode {
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
  private sandboxNoticeGiven = false;
  constructor(private readonly deps: ApprovalDependencies) {
    this.view = { tier: "unknown", snapshot: null, mode: null, confirmed: false, pending: false,
      warningDismissed: deps.warningDismissed(), trusted: deps.isTrusted(), error: null, notice: null };
  }
  current(): ApprovalView { return this.view; }
  private publish(patch: Partial<ApprovalView>): void {
    const next = { ...this.view, ...patch, warningDismissed: this.deps.warningDismissed(), trusted: this.deps.isTrusted() };
    // The /live poll observes every second; re-rendering an unchanged view re-renders the whole webview.
    if (JSON.stringify(next) === JSON.stringify(this.view)) return;
    this.view = next;
    this.deps.render(this.view);
  }
  /** Full access and Auto never apply to an untrusted workspace, and Auto only where the
   * backend offers it; the saved preference is kept either way. */
  private allowedMode(mode: ApprovalMode, state?: ApprovalSnapshot | null): ApprovalMode {
    if (needsTrust(mode) && !this.deps.isTrusted()) return "accept_edits";
    if (mode === "auto" && !offersAuto(state ?? this.view.snapshot)) return "accept_edits";
    return mode;
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
    this.noticeSandboxUnavailable(snapshot);
  }
  private noticeSandboxUnavailable(snapshot: ApprovalSnapshot): void {
    const sandbox = snapshot.sandbox;
    // No reason yet means the startup check is still running.
    if (this.sandboxNoticeGiven || !sandbox?.enabled || sandbox.available || !sandbox.reason) return;
    this.sandboxNoticeGiven = true;
    this.deps.notifySandboxUnavailable(sandbox.reason);
  }
  /** A backend without a sandbox gets nothing, so an older one never sees the field. */
  private sandboxField(state: ApprovalSnapshot): { sandbox?: boolean } {
    return state.sandbox ? { sandbox: this.deps.sandboxEnabled() } : {};
  }
  private sandboxAgrees(state: ApprovalSnapshot): boolean {
    return !state.sandbox || state.sandbox.enabled === this.deps.sandboxEnabled();
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
            this.publish({ tier: "older", snapshot: null, mode: needsTrust(mode) ? "accept_edits" : mode,
              confirmed: false, pending: false });
          }
          return;
        }
        const ownedMode = state.owned ? modeFor(state) : null;
        if (state.owned && ownedMode === this.allowedMode(ownedMode ?? "ask", state) && this.sandboxAgrees(state)) {
          this.adopt(state);
          if (!this.deps.savedMode() && ownedMode) await this.persist(ownedMode);
          this.publish({ pending: false });
          return;
        }
        const saved = this.deps.savedMode();
        const stored = saved ?? migrateApproval(state.shellPolicy, this.deps.reviewOn());
        const preferred = state.owned ? ownedMode! : stored === "full_access" ? "ask" : stored;
        const mode = this.allowedMode(preferred, state);
        try {
          const applied = await client.setApprovalMode({ ...policyFor(mode), ...this.sandboxField(state), resolveWaiting: false,
            expectedProcessId: state.processId, expectedVersion: state.version });
          if (this.endpointChanged()) continue;
          this.adopt(applied);
          if (mode === preferred) await this.persist(mode);
          // An untrusted workspace's fallback is explained by view.trusted, not a notice.
          this.publish({ pending: false, notice: mode === preferred && !saved && state.shellPolicy === "allow_all" && this.deps.reviewOn()
            ? "Commands now ask for approval. You can change this in approval modes." : null });
          return;
        } catch (error) {
          const current = this.conflict(error);
          if (!current) throw error;
          if (current.owned && modeFor(current) === this.allowedMode(modeFor(current) ?? "ask", current) && this.sandboxAgrees(current)) {
            this.adopt(current); this.publish({ pending: false }); return;
          }
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
    if (this.view.mode && needsTrust(this.view.mode) && !this.deps.isTrusted()) void this.connect().catch(() => undefined);
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
        if (mode === "full_access" && !this.deps.isTrusted()) throw new Error("Full access needs a trusted workspace");
        if (mode === "auto" && !this.deps.isTrusted()) throw new Error("Auto needs a trusted workspace");
        if (mode === "auto" && this.view.tier !== "older" && !offersAuto(this.view.snapshot)) {
          throw new Error("Auto requires an updated backend");
        }
        if (mode === "full_access" && !this.deps.warningDismissed() && !fullAccessConfirmed) throw new Error("Confirm Full access first");
        const client = this.deps.client();
        if (this.view.tier === "older") {
          if (mode === "full_access") throw new Error("Full access requires an updated backend");
          if (mode === "auto") throw new Error("Auto requires an updated backend");
          await client.setGlobalReviewPref({ autoAccept: mode === "accept_edits" });
          await this.persist(mode);
          this.publish({ mode, confirmed: true, pending: false });
          return;
        }
        const before = this.view.snapshot!;
        try {
          const applied = await client.setApprovalMode({ ...policyFor(mode), ...this.sandboxField(before), resolveWaiting: true,
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
  /** The switch persists only after the backend acknowledges. Throws, after showing the
   * error, so Settings can keep it beside the switch. */
  setSandboxEnabled(enabled: boolean): Promise<void> {
    return this.enqueue(async () => {
      this.publish({ pending: true, error: null, notice: null });
      try {
        if (this.endpointChanged() || this.view.tier === "unknown" || (this.view.tier === "supported" && !this.view.confirmed)) await this.connectNow();
        await this.applySandbox(enabled, true);
        if (this.deps.sandboxEnabled() !== enabled) {
          try { await this.deps.saveSandboxEnabled(enabled); }
          catch (error) { this.publish({ error: `Applied, but the preference could not be saved: ${error instanceof Error ? error.message : String(error)}` }); }
        }
        this.publish({ pending: false });
      } catch (error) {
        this.publish({ pending: false, error: `Not applied — retry: ${error instanceof Error ? error.message : String(error)}` });
        throw error;
      }
    });
  }
  private async applySandbox(enabled: boolean, mayRetry: boolean): Promise<void> {
    const before = this.view.snapshot;
    if (this.view.tier !== "supported" || !before?.sandbox) throw new Error("The sandbox needs an updated backend");
    if (before.sandbox.enabled === enabled) return;
    try {
      // The mode stays what the backend has; only the switch changes.
      this.adopt(await this.deps.client().setApprovalMode({ autoAccept: before.autoAccept, shellPolicy: before.shellPolicy,
        sandbox: enabled, resolveWaiting: false, expectedProcessId: before.processId, expectedVersion: before.version }));
    } catch (error) {
      const current = this.conflict(error);
      if (!current || !mayRetry) throw error;
      if (current.processId === before.processId) this.adopt(current);
      else await this.connectNow();
      await this.applySandbox(enabled, false);
    }
  }
  /** Trust only enables an explicit future selection; it never promotes old policy. */
  trustGranted(): Promise<void> { this.publish({});return this.connect(); }
  /** A user's own Full access choice in this workspace is its confirmation; no first-send dialog follows. */
  async acknowledgeUserSelection(): Promise<void> {
    if (this.view.mode !== "full_access") return;
    try { await this.deps.acknowledgeFirstSend(); }
    catch { this.publish({ error: "Applied, but the Full access confirmation could not be saved." }); }
  }
  /** The first message sent under Full access in a workspace asks once, so a preference carried over
   * from another workspace never runs commands here unannounced. False means do not send. */
  async confirmFirstSend(): Promise<boolean> {
    if (this.view.mode !== "full_access" || this.deps.firstSendAcknowledged()) return true;
    const choice = await this.deps.confirmFirstFullAccessSend();
    if (choice === "continue") {
      await this.deps.acknowledgeFirstSend();
      return true;
    }
    if (choice === "cancel") return false;
    await this.select("accept_edits");
    return this.current().mode === "accept_edits";
  }
}
