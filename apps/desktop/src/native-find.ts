export interface FindContents {
  findInPage(query: string, options: { forward: boolean; findNext: boolean }): number;
  stopFindInPage(action: "clearSelection"): void;
}
/** One current native request; stale native results cannot republish after navigation/close. */
export class NativeFind {
  private active: { nativeId: number; requestId: string; viewId: string } | undefined;
  constructor(private readonly contents: FindContents, private readonly view: () => string, private readonly post: (payload: Record<string, unknown>) => void) {}
  stop(): void { this.active=undefined; this.contents.stopFindInPage("clearSelection"); }
  search(request: { requestId: string; query: string; forward: boolean; next: boolean }): void {
    if (!request.query.trim()) { this.stop(); return; }
    const viewId=this.view();
    try { const nativeId=this.contents.findInPage(request.query,{forward:request.forward,findNext:request.next}); this.active={nativeId,requestId:request.requestId,viewId}; }
    catch { this.active=undefined;this.post({type:"desktop/findResult",requestId:request.requestId,error:"Find is unavailable in this view. Reopen it and try again."}); }
  }
  result(result: {requestId: number; activeMatchOrdinal: number; matches: number; finalUpdate?: boolean}): void {
    const active=this.active;
    if (!active || result.requestId!==active.nativeId || active.viewId!==this.view()) return;
    this.post({type:"desktop/findResult",requestId:active.requestId,matches:result.matches,ordinal:result.activeMatchOrdinal,finalUpdate:result.finalUpdate ?? true});
  }
}
