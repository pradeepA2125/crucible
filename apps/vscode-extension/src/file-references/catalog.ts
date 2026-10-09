import type { FileReferenceEntry } from "./matching.js";

export interface RetainedFile<U> { entry: FileReferenceEntry; uri: U; root: U; }
export interface CatalogSnapshot<U> { revision: number; files: readonly RetainedFile<U>[]; }
export interface CatalogPort<U> {
  enumerate(): Promise<RetainedFile<U>[]>;
  onInvalidate(listener: () => void): { dispose(): void };
  reportFailure(error: unknown): void;
}

/** One extension-owned inventory, independent of message renders or panel lifetime. */
export class FileReferenceCatalog<U> {
  private snapshot: CatalogSnapshot<U> = { revision: 0, files: [] };
  private generation = 0;
  private snapshotGeneration = -1;
  private disposed = false;
  private inFlight: Promise<CatalogSnapshot<U>> | undefined;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private readonly listeners = new Set<(snapshot: CatalogSnapshot<U>) => void>();
  private readonly subscription: { dispose(): void };

  constructor(private readonly port: CatalogPort<U>) {
    this.subscription = port.onInvalidate(() => this.invalidate());
  }

  /** Clicks reuse a current inventory; only membership changes require a scan.
   * The chosen file is still stat/containment-checked immediately before opening. */
  current(): Promise<CatalogSnapshot<U>> {
    if (this.disposed || this.snapshotGeneration === this.generation) {
      return Promise.resolve(this.snapshot);
    }
    return this.refresh();
  }

  refresh(): Promise<CatalogSnapshot<U>> {
    this.clearTimer();
    if (this.disposed) return Promise.resolve(this.snapshot);
    if (this.inFlight) return this.inFlight;
    const pending = this.enumerateCurrent();
    this.inFlight = pending;
    // Register cleanup without manufacturing an unhandled rejected promise.
    void pending.then(
      () => { if (this.inFlight === pending) this.inFlight = undefined; },
      () => { if (this.inFlight === pending) this.inFlight = undefined; },
    );
    return pending;
  }

  private async enumerateCurrent(): Promise<CatalogSnapshot<U>> {
    while (!this.disposed) {
      const generation = this.generation;
      let files: RetainedFile<U>[];
      try {
        files = await this.port.enumerate();
      } catch (error) {
        files = [];
        this.port.reportFailure(error);
      }
      if (this.disposed) return this.snapshot;
      if (generation !== this.generation) {
        this.clearTimer();
        continue;
      }
      this.snapshot = { revision: this.snapshot.revision + 1, files };
      this.snapshotGeneration = generation;
      for (const listener of this.listeners) listener(this.snapshot);
      return this.snapshot;
    }
    return this.snapshot;
  }

  invalidate(): void {
    if (this.disposed) return;
    this.generation++;
    this.clearTimer();
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.refresh().catch(error => this.port.reportFailure(error));
    }, 100);
  }

  subscribe(listener: (snapshot: CatalogSnapshot<U>) => void): { dispose(): void } {
    if (!this.disposed) this.listeners.add(listener);
    return { dispose: () => { this.listeners.delete(listener); } };
  }

  private clearTimer(): void {
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.clearTimer();
    this.subscription.dispose();
    this.listeners.clear();
  }
}
