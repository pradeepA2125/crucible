// vscode-free. The one sink every client reports auth status to (spec §3.8).
export class AuthStatusSink {
  private readonly notified = new Set<string>();

  constructor(private readonly ui: {
    setError(reason: string | null): void;
    notify(base: string, reason: string): void;
  }) {}

  report(base: string, ok: boolean, reason?: string): void {
    if (ok) {
      this.ui.setError(null);
      return;
    }
    const why = reason ?? "not authorized";
    this.ui.setError(why);
    if (!this.notified.has(base)) {
      this.notified.add(base);
      this.ui.notify(base, why);
    }
  }
}
