import { ZodError } from "zod";
import { randomUUID } from "node:crypto";
import { APP_ORIGIN, CHANNEL, parseEnvelope, type BridgeContext } from "./contract.js";
import { assertSender } from "./security.js";
import { parseInspector } from "./inspector-protocol.js";
import { handleMemoryMessage, type MemoryDataSource, type MemoryToHost } from "../../vscode-extension/src/memory-data.js";
import { handleGraphMessage, type GraphHostDeps, type GraphToHost } from "../../vscode-extension/src/graph/graph-messages.js";
export interface InspectorWindow {
  webContents: { id: number; mainFrame: unknown; getURL(): string; send(channel: string, message: unknown): void; on(name: string, callback: (...args: any[]) => void): unknown };
  isDestroyed(): boolean; focus(): void; show(): void; close(): void; loadURL(url: string): Promise<unknown>; on(name: string, callback: () => void): unknown;
}
export interface InspectorEvent { sender: { id: number }; senderFrame: { url: string } | null; }
interface Session { window: InspectorWindow; context: BridgeContext; kind: "memory" | "graph"; root: string; memory: MemoryDataSource; graph: GraphHostDeps; sequence: number; busy: boolean; readingView?: string | undefined; pendingReady?: string | undefined; unwatch?: () => void; }
/** Isolated readonly documents. Leases outlive a closed window until actual work settles. */
export class Inspectors {
  private sessions = new Map<number, Session>();
  private leases = 0;
  constructor(private readonly create: (kind: "memory" | "graph", threadId: string) => InspectorWindow, private readonly theme: () => Record<string, unknown>) {}
  owns(id: number): boolean { return this.sessions.has(id); }
  bootstrap(event: InspectorEvent): BridgeContext { const s = this.sender(event); return { ...s.context }; }
  private sender(event: InspectorEvent): Session {
    const s = this.sessions.get(event.sender.id); if (!s || s.window.isDestroyed()) throw new Error("Inspector is closed.");
    assertSender({ id: event.sender.id, frameIsMain: event.senderFrame === s.window.webContents.mainFrame, url: event.senderFrame?.url ?? "" }, s.window.webContents.id);
    if (new URL(event.senderFrame!.url).pathname !== `/${s.kind}.html`) throw new Error("Wrong inspector document.");
    return s;
  }
  async open(kind: "memory" | "graph", context: BridgeContext, root: string, memory: MemoryDataSource, graph: GraphHostDeps & { watch?(changed: () => void): () => void }): Promise<void> {
    const old = [...this.sessions.values()].find(s => s.kind === kind && s.context.projectId === context.projectId && s.context.threadId === context.threadId);
    if (old) { old.window.show(); old.window.focus(); return; }
    if (this.sessions.size >= 2 || this.leases >= 2) throw new Error("Close an inspector or wait for its current read to finish.");
    const window = this.create(kind, context.threadId ?? "no conversation"), s: Session = { window, kind, root, memory, graph, context: { ...context, viewId: randomUUID() }, sequence: 0, busy: false };
    const contentsId = window.webContents.id;
    this.sessions.set(contentsId, s);
    window.webContents.on("did-start-navigation", (_event, _url, inPlace, main) => { if (main && !inPlace) { s.context.viewId = randomUUID(); s.sequence = 0; } });
    window.on("closed", () => { s.unwatch?.(); this.sessions.delete(contentsId); });
    if (kind === "graph" && graph.watch) s.unwatch = graph.watch(() => {
      if (s.busy || this.leases >= 2 || !this.sessions.has(contentsId)) return;
      s.busy = true; this.leases++; const view=s.context.viewId; s.readingView=view;
      void handleGraphMessage({type:"refresh"},graph,m=>this.post(s,m,view)).finally(()=>{s.busy=false;s.readingView=undefined;this.leases--;this.hydratePending(s);});
    });
    try { await window.loadURL(`${APP_ORIGIN}/${kind}.html`); } catch (error) { this.sessions.delete(contentsId); window.close(); throw error; }
  }
  private post(s: Session, payload: unknown, view = s.context.viewId): void {
    if (!s.window.isDestroyed() && this.sessions.get(s.window.webContents.id) === s && s.context.viewId === view) s.window.webContents.send(CHANNEL.event, { context: { ...s.context }, payload });
  }
  private hydratePending(s: Session): void {
    const view = s.pendingReady; s.pendingReady = undefined;
    if (!view || view !== s.context.viewId || s.window.isDestroyed() || this.sessions.get(s.window.webContents.id) !== s) return;
    s.busy = true; s.readingView = view; this.leases++;
    const action = s.kind === "memory" ? handleMemoryMessage({type:"ready"}, s.memory, s.context.threadId ?? "", s.root, m => this.post(s,m,view)) : handleGraphMessage({type:"ready"},s.graph,m=>this.post(s,m,view));
    const timer=setTimeout(()=>this.post(s,{type:"error",resource:"browser",message:"Inspector timed out. Its previous read is still settling."},view),15000);
    void action.catch(error => this.post(s,{type:"error",resource:"browser",message:error instanceof Error ? error.message : "Inspection unavailable."},view)).finally(()=>{clearTimeout(timer);s.busy=false;s.readingView=undefined;this.leases--;this.hydratePending(s);});
  }
  broadcastTheme(): void { for (const s of this.sessions.values()) this.post(s, this.theme()); }
  async handle(event: InspectorEvent, raw: unknown): Promise<{ error?: string }> {
    let s: Session | undefined, requestView: string | undefined;
    try {
      s = this.sender(event); requestView = s.context.viewId; const envelope = parseEnvelope(raw, s.context, s.sequence); s.sequence = envelope.sequence;
      if (envelope.payload.type === "themeReady") { this.post(s, this.theme()); return {}; }
      const payload = parseInspector(envelope.payload, s.kind);
      if (s.busy && payload.type === "ready") {
        // Only same-document duplicates coalesce. A reload keeps one latest hydration.
        if (s.readingView !== s.context.viewId) s.pendingReady = s.context.viewId;
        return {};
      }
      if (s.busy || this.leases >= 2) throw new Error("Inspector is still reading. Refresh when it finishes.");
      s.busy = true; this.leases++; const session = s, view = s.context.viewId; s.readingView = view;
      const action = s.kind === "memory" ? handleMemoryMessage(payload as MemoryToHost, s.memory, s.context.threadId ?? "", s.root, m => this.post(session, m, view))
        : handleGraphMessage(payload as GraphToHost, s.graph, m => this.post(session, m, view));
      const actual = action.finally(() => { session.busy = false; session.readingView = undefined; this.leases--; this.hydratePending(session); });
      let timer: ReturnType<typeof setTimeout> | undefined;
      try { await Promise.race([actual, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("Inspector timed out. Its previous read is still settling.")), 15000); })]); }
      finally { clearTimeout(timer); }
      return {};
    } catch (error) {
      const message = error instanceof ZodError ? "This inspector action is unavailable or malformed." : error instanceof Error ? error.message : "Inspection unavailable.";
      if (s) this.post(s, { type: "error", resource: "browser", message }, requestView);
      return { error: message };
    }
  }
  dispose(): void { for (const s of this.sessions.values()) s.window.close(); this.sessions.clear(); }
}
