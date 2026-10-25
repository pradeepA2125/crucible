import { appendFileSync, chmodSync, mkdirSync, statSync, renameSync, rmSync } from "node:fs";
import { join } from "node:path";
const EVENTS = new Set(["main-start","renderer-gone","unresponsive","responsive","window-state-error"]);
/** Metadata only: no crash dumps, transcript, filesystem paths, tokens or network uploader. */
export function localDiagnostics(profile:string, now:()=>number=Date.now) {
  const directory=join(profile,"diagnostics"),file=join(directory,"events.jsonl");
  return (event:string,details: {code?:number;reason?:string}={})=>{
    if(!EVENTS.has(event))return;
    try{mkdirSync(directory,{recursive:true,mode:0o700});
      try{if(statSync(file).size>256*1024){rmSync(file+".previous",{force:true});renameSync(file,file+".previous");}}catch(error){if((error as NodeJS.ErrnoException).code!=="ENOENT")throw error;}
      const record={at:now(),event,...(Number.isSafeInteger(details.code)?{code:details.code}:{}),...(details.reason && /^[a-z-]{1,40}$/.test(details.reason)?{reason:details.reason}:{})};
      appendFileSync(file,JSON.stringify(record)+"\n",{mode:0o600});chmodSync(file,0o600);
    }catch{/* Diagnostics must not block shutdown or reveal paths in a fallback log. */}
  };
}
