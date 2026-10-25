import { terminalResult, type TerminalRequest } from "../../../../desktop/src/terminal-protocol";
export type TerminalData = ReturnType<typeof terminalResult.parse>;
export interface TerminalPort { request(fields: Omit<TerminalRequest,"requestId">): Promise<TerminalData>; dispose(): void; }
export function createTerminalClient(send:(value:unknown)=>void,target:Window=window):TerminalPort {
  let disposed=false,context:string|undefined;
  const pending=new Map<string,{finish(value?:TerminalData,error?:string):void}>();
  const fail=(message:string)=>{for(const p of [...pending.values()])p.finish(undefined,message);};
  const listener=(e:MessageEvent)=>{const m=e.data;
    if(m?.type==="desktop/baselineContext") {const next=JSON.stringify([m.projectId,m.viewId,m.threadId]);if(context!==undefined&&context!==next)fail("Project or conversation changed. Reopen Terminal.");context=next;}
    if(m?.type!=="desktop/terminalResult" || typeof m.requestId!=="string")return;
    const p=pending.get(m.requestId);if(!p)return;
    if(typeof m.error==="string")p.finish(undefined,m.error);
    else {const result=terminalResult.safeParse(m.result);p.finish(result.success?result.data:undefined,result.success?undefined:"Terminal reply was invalid. Refresh its session.");}
  };
  target.addEventListener("message",listener);
  return {request:fields=>new Promise((resolve,reject)=>{
    if(disposed || pending.size>=8){reject(new Error("Terminal view is closed or busy."));return;}
    const requestId=crypto.randomUUID(),timer=setTimeout(()=>pending.get(requestId)?.finish(undefined,"Terminal action timed out. Refresh before retrying; launch or input may already have reached the shell."),fields.operation==="start"?120000:6000);
    pending.set(requestId,{finish:(result,error)=>{clearTimeout(timer);pending.delete(requestId);if(error)reject(new Error(error));else resolve(result??{});}});
    try{send({type:"desktop/terminal",requestId,...fields});}catch{pending.get(requestId)?.finish(undefined,"Terminal bridge is unavailable.");}
  }),dispose:()=>{disposed=true;target.removeEventListener("message",listener);fail("Terminal view closed.");}};
}
