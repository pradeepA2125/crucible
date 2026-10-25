import { probeHealth } from "./probe-health.js";
import { randomBytes, randomUUID } from "node:crypto";
export interface OwnerCapability { key:string; indexKey:string; clientId:string; name:string; workspace:string; pid:number; }
export interface ProjectSession { protocol:1; workspace:string; process_id:string; pid:number; client_id:string; client_name:string; role:"owner"|"observer"; trusted:boolean; }
const owners=new Map<string,OwnerCapability>();
export function newOwner(name:string,workspace:string):OwnerCapability {return {key:randomBytes(32).toString("hex"),indexKey:randomBytes(32).toString("hex"),clientId:randomUUID(),name,workspace,pid:0};}
export function registerOwner(port:number,owner:OwnerCapability):void {owners.set(`http://127.0.0.1:${port}`,owner);}
export function forgetOwner(port:number,owner:OwnerCapability):void {const base=`http://127.0.0.1:${port}`;if(owners.get(base)===owner)owners.delete(base);}
export function ownerFor(url:string):OwnerCapability|undefined {const u=new URL(url);if(u.protocol!=="http:" || u.hostname!=="127.0.0.1" || u.username || u.password)return;return owners.get(u.origin);}
export function ownerHeaders(url:string):Record<string,string> {const owner=ownerFor(url);return owner ? {"x-crucible-owner":owner.key} : {};}
export function parseProjectSession(raw:unknown):ProjectSession {
 const x=raw as ProjectSession;
 if(!x || x.protocol!==1 || typeof x.workspace!=="string" || !x.workspace || typeof x.process_id!=="string" || !x.process_id || !Number.isSafeInteger(x.pid) || x.pid<1 || typeof x.client_id!=="string" || typeof x.client_name!=="string" || !["owner","observer"].includes(x.role) || typeof x.trusted!=="boolean")throw new Error("This backend needs an ownership-compatible runtime update. Inspection is read only.");
 return x;
}
export function assertOwnerSession(raw:unknown,owner:OwnerCapability):ProjectSession {const x=parseProjectSession(raw);if(x.role!=="owner" || x.pid!==owner.pid || x.workspace!==owner.workspace || x.client_id!==owner.clientId)throw new Error("This project's owner changed. Reopen it in its controlling client.");return x;}

/** Prove the listening process/root without disclosing either credential first. */
export async function verifyOwnerEndpoint(base: string, owner: OwnerCapability,
  fetcher: (url: string, init?: RequestInit) => Promise<Response>,
  readToken: (port: number) => string | undefined, signal?: AbortSignal | null): Promise<void> {
  const check = () => {
    signal?.throwIfAborted();
    if (ownerFor(base) !== owner) throw new Error("This project's owner changed. Reopen its controlling client.");
  };
  check();
  const result = await probeHealth(Number(new URL(base).port), {
    readToken,
    fetchRaw: async (url, init) => {
      const response = await fetcher(url, { signal: init.signal, redirect: "manual" });
      return { status: response.status, body: await response.text() };
    },
  }, { pid: owner.pid, workspace: owner.workspace });
  check();
  if (result !== "authed") throw new Error("The backend did not prove this project's owning process. Reopen the controlling client.");
}
