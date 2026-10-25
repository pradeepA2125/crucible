import type { LifecycleEvidence } from "./lifecycle-protocol.js";
export interface ProjectActivity { state: "idle" | "busy" | "unknown"; attention: Array<{ threadId: string; title: string; gates: number }>; evidence?: LifecycleEvidence; }
export interface RetainedProject {
  canDetach(): boolean; detach(): void; flush(): void; activity(): Promise<ProjectActivity>;
  pause(): Promise<boolean>; dispose(discardDrafts?:boolean): Promise<void>;
}
interface Slot<T> { host: T; backgroundAt: number; idleAt?: number; paused: boolean; reading: boolean; status: ProjectActivity; }
/** Hosts retain drafts/controllers. Only the selected slot may publish to the view.
 * An unresolved read owns its slot until actual settlement; no timeout work pileup. */
export class ProjectPool<T extends RetainedProject> {
  private slots = new Map<string, Slot<T>>();
  selected: string | undefined;
  private changing = false;
  constructor(private readonly now: () => number = () => performance.now(), private readonly cap = 8) {}
  get(id: string): T | undefined { return this.slots.get(id)?.host; }
  isSelected(id: string): boolean { return this.selected === id; }
  canAdd(id: string): void { if (!this.slots.has(id) && this.slots.size >= this.cap) throw new Error("Eight projects are already loaded. Remove an idle project from Recents to free a slot; its repository is kept."); }
  add(id: string, host: T): void { this.canAdd(id); if (this.slots.has(id)) throw new Error("Project is already loaded."); this.slots.set(id, { host, backgroundAt: this.now(), paused: false, reading: false, status: {state:"unknown",attention:[]} }); }
  async select(id: string, flushView: () => Promise<void>, attach: (host: T) => Promise<void>, current:()=>boolean = ()=>true): Promise<void> {
    if (this.changing) throw new Error("Project navigation is already in progress.");
    const next = this.slots.get(id); if (!next) throw new Error("Project is not loaded.");
    this.changing = true;
    try {
      const old = this.selected && this.slots.get(this.selected);
      if (old && !old.host.canDetach()) throw new Error("Finish the pending send or project action before switching projects.");
      await flushView();
      if(!current())return;
      if (old && !old.host.canDetach()) throw new Error("A project action started while saving. Retry switching.");
      old && old.host.flush(); next.host.flush();
      if (old) { old.host.detach(); old.backgroundAt = this.now(); delete old.idleAt; }
      this.selected = id; next.paused = false; delete next.idleAt;
      await attach(next.host);
    } finally { this.changing = false; }
  }
  summaries(): Array<{ id: string; paused: boolean } & ProjectActivity> { return [...this.slots].map(([id,s]) => ({ id, paused:s.paused, state:s.status.state,attention:s.status.attention })); }
  observations(): Array<{id:string;paused:boolean;activity:ProjectActivity}> { return [...this.slots].map(([id,s])=>({id,paused:s.paused,activity:s.status})); }
  async refresh():Promise<Array<{id:string;paused:boolean;activity:ProjectActivity}>> {
    return Promise.all([...this.slots].map(async ([id,s])=>({id,paused:s.paused,activity:s.paused?{state:"idle" as const,attention:[]}:await s.host.activity().catch(():ProjectActivity=>({state:"unknown",attention:[]}))})));
  }
  async poll(): Promise<void> {
    await Promise.allSettled([...this.slots].map(async ([id,s]) => {
      if (s.reading || s.paused) return;
      s.reading = true; s.status={state:"unknown",attention:[]};
      try {
        s.status = await s.host.activity();
        if (this.selected === id || s.status.state !== "idle" || !s.host.canDetach()) { delete s.idleAt; return; }
        s.idleAt ??= this.now();
        if (!this.changing && this.now() - Math.max(s.idleAt, s.backgroundAt) >= 300000) {
          // Host rechecks fresh activity and owning PID immediately before pause.
          this.changing=true;
          try { s.host.flush(); s.paused = await s.host.pause(); if (!s.paused) delete s.idleAt; }
          finally { this.changing=false; }
        }
      } catch { s.status = { state:"unknown", attention:[] }; delete s.idleAt; }
      finally { s.reading = false; }
    }));
  }
  async remove(id: string): Promise<void> {
    if (this.changing || this.selected === id) throw new Error("Switch to another project before removing this project.");
    const s = this.slots.get(id); if (!s) return;
    if (!s.host.canDetach() || s.reading) throw new Error("Wait for this project's pending action or status check.");
    this.changing = true;
    try {
      const status = s.paused ? {state:"idle"} : await s.host.activity();
      if (status.state !== "idle" || !s.host.canDetach()) throw new Error("This project is busy or its activity is unknown. It was kept.");
      s.host.flush(); await s.host.dispose(); this.slots.delete(id);
    } finally { this.changing = false; }
  }
  flush(): void {const errors:unknown[]=[];for(const s of this.slots.values()){try{s.host.flush();}catch(error){errors.push(error);}}
    if(errors.length)throw new AggregateError(errors,"Project drafts could not be saved. Retry saving or explicitly discard the latest unsaved edits.");}
  async dispose(discardDrafts=false): Promise<void> {
    const results = await Promise.allSettled([...this.slots].map(async ([id, s]) => {
      await s.host.dispose(discardDrafts); this.slots.delete(id);
    }));
    const errors = results.flatMap(r => r.status === "rejected" ? [r.reason] : []);
    if (errors.length) throw new AggregateError(errors, "Some projects could not be closed. Retry quitting.");
  }
}
