export interface Bounds { x: number; y: number; width: number; height: number; }
export interface WindowState { version: 1; bounds: Bounds; maximized: boolean; }
const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n) && Math.abs(n) < 1e7;
/** Only normal bounds are persisted. Screen topology is authoritative on every launch. */
export function restoredWindow(value: unknown, areas: Bounds[]): { bounds: Bounds; maximized: boolean } {
  const displays = areas.filter(a => Object.values(a).every(finite) && a.width > 0 && a.height > 0);
  if (!displays.length) throw new Error("No usable display work area.");
  const raw = value as Partial<WindowState> | null;
  const b = raw?.version === 1 ? raw.bounds : undefined;
  const valid = b && [b.x,b.y,b.width,b.height].every(finite) && b.width >= 200 && b.height >= 200;
  const target = valid ? displays.reduce((best,a) => overlap(b,a)>overlap(b,best)?a:best,displays[0]!) : displays[0]!;
  const width = Math.round(Math.min(Math.max(valid ? b.width : 1440,720),target.width));
  const height = Math.round(Math.min(Math.max(valid ? b.height : 900,560),target.height));
  const seen = valid && overlap(b,target)>0;
  const x = Math.round(seen ? Math.max(target.x,Math.min(b.x,target.x+target.width-width)) : target.x+(target.width-width)/2);
  const y = Math.round(seen ? Math.max(target.y,Math.min(b.y,target.y+target.height-height)) : target.y+(target.height-height)/2);
  return {bounds:{x,y,width,height},maximized:!!valid && raw?.maximized===true};
}
function overlap(a:Bounds,b:Bounds):number {return Math.max(0,Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y));}
export interface WindowPort {getNormalBounds(): Bounds; isMaximized(): boolean; isMinimized(): boolean; isFullScreen(): boolean; on(event:string, listener:()=>void): unknown;}
export function rememberWindow(window: WindowPort, save:(state:WindowState)=>PromiseLike<void>, failed:(error:unknown)=>void) {
  let timer:ReturnType<typeof setTimeout>|undefined,latest:WindowState|undefined,chain=Promise.resolve();
  const capture=()=>{if(window.isMinimized()||window.isFullScreen())return;const bounds=window.getNormalBounds();if(![bounds.x,bounds.y,bounds.width,bounds.height].every(finite))return;latest={version:1,bounds,maximized:window.isMaximized()};};
  const flush=()=>{clearTimeout(timer);timer=undefined;capture();const state=latest;latest=undefined;if(state)chain=chain.then(()=>save(state)).catch(failed);return chain;};
  const changed=()=>{capture();clearTimeout(timer);timer=setTimeout(()=>void flush(),250);};
  for(const event of ["move","resize","maximize","unmaximize","leave-full-screen"])window.on(event,changed);
  return {flush};
}
