// vscode-free. No request — and no token — goes to a backend URL until /health has
// proven the server there holds the token (spec §3.5 "Verify before sending").
import { ownerFor,assertOwnerSession,verifyOwnerEndpoint } from "../runtime/project-owner.js";
import { BackendAuthError } from "@crucible/editor-client";
import type { ProbeResult } from "../runtime/probe-health.js";

export const REPROBE_INTERVAL_MS = 5000;

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface GateDeps {
  fetch: FetchLike;
  probe: (port: number) => Promise<ProbeResult>;
  readToken: (port: number) => string | undefined;
  report: (base: string, ok: boolean, reason?: string) => void;
  now: () => number;
}

export function normalizeBackendUrl(url: string): { base: string; port: number; isLoopback: boolean } {
  const parsed = new URL(url.trim());
  if (parsed.hostname === "localhost") parsed.hostname = "127.0.0.1";
  const port = parsed.port ? Number(parsed.port) : parsed.protocol === "https:" ? 443 : 80;
  const base = `${parsed.protocol}//${parsed.host}${parsed.pathname}`.replace(/\/+$/, "");
  return { base, port, isLoopback: parsed.hostname === "127.0.0.1" };
}

interface BaseState {
  verified: boolean;
  lastProbeAt: number;
  lastResult: ProbeResult | null;
  inFlight: Promise<ProbeResult> | null;
}

const REASONS: Record<Exclude<ProbeResult, "authed" | "down">, string> = {
  unauthorized: "the backend did not prove it holds this window's token",
  preauth: "the backend answered without a token proof (an old or foreign server)",
  "no-token": "no readable token file for this backend's port",
  "other-backend": "a different backend now holds this port",
};

export class BackendGate {
  private readonly states = new Map<string, BaseState>();

  constructor(private readonly deps: GateDeps) {}

  tokenFor(base: string): () => string | undefined {
    const { port, isLoopback } = normalizeBackendUrl(base);
    return () => (isLoopback ? this.deps.readToken(port) : undefined);
  }

  fetchFor(base: string): FetchLike {
    const target = normalizeBackendUrl(base);
    return async (input, init) => {
      init?.signal?.throwIfAborted();
      const request=new URL(input),baseURL=new URL(target.base);
      if(request.origin!==baseURL.origin || request.username || request.password || !request.pathname.startsWith(baseURL.pathname.replace(/\/$/,"")+"/")) throw new BackendAuthError("Request is outside the verified project backend.",null);
      if (!target.isLoopback) {
        const reason = "the backend must be at 127.0.0.1";
        this.deps.report(target.base, false, reason);
        throw new BackendAuthError(reason, null);
      }
      const result = await this.verify(target.base, target.port);
      // The shared proof belongs to every waiter; cancellation only stops this request.
      init?.signal?.throwIfAborted();
      if (result === "down") {
        throw new TypeError(`fetch failed: backend at ${target.base} is not reachable`);
      }
      if (result !== "authed") {
        this.deps.report(target.base, false, REASONS[result]);
        throw new BackendAuthError(REASONS[result], null);
      }
      const headers=new Headers(init?.headers);headers.delete("x-crucible-owner");
      const owner=ownerFor(target.base), mutation=!["GET","HEAD"].includes((init?.method ?? "GET").toUpperCase());
      if(owner && (mutation || request.pathname.endsWith("/project/session")))
        await verifyOwnerEndpoint(target.base,owner,this.deps.fetch,this.deps.readToken,init?.signal);
      if(owner && ownerFor(target.base)!==owner)throw new BackendAuthError("Project ownership changed while the proof was waiting.",409);
      if(mutation){
        if(!owner)throw new BackendAuthError("This client is an observer. Use the controlling client, or close it and reopen the project here. Older backends require a runtime update.",409);
        const token=this.deps.readToken(target.port);
        const response=await this.deps.fetch(`${target.base}/v1/project/session`,{headers:{"x-crucible-owner":owner.key,...(token?{authorization:`Bearer ${token}`}:{})},redirect:"manual",signal:AbortSignal.timeout(3000)});
        if(!response.ok)throw new BackendAuthError("Project ownership cannot be verified. Update the runtime or reopen its controlling client.",409);
        assertOwnerSession(await response.json(),owner);
        if(ownerFor(target.base)!==owner)throw new BackendAuthError("Project ownership changed while the request was waiting.",409);
      }
      init?.signal?.throwIfAborted();
      if(owner && ownerFor(target.base)!==owner)throw new BackendAuthError("Project ownership changed while the request was waiting.",409);
      if(owner && (mutation || request.pathname.endsWith("/project/session")))headers.set("x-crucible-owner",owner.key);
      try {
        return await this.deps.fetch(input, {...init,headers:Object.fromEntries(headers.entries()),redirect:"manual"});
      } catch (err) {
        // A respawn may hand the port to someone else: forget the old verdict so the
        // next request probes again instead of reusing a cached "authed".
        const st = this.state(target.base);
        st.verified = false;
        st.lastResult = null;
        throw err;
      }
    };
  }

  authedFetchFor(base: string): FetchLike {
    const fetchVerified = this.fetchFor(base);
    const token = this.tokenFor(base);
    return (input, init) => {
      const headers = { ...((init?.headers as Record<string, string> | undefined) ?? {}) };
      const value = token();
      if (value) headers.authorization = `Bearer ${value}`;
      return fetchVerified(input, { ...init, headers, redirect: "manual" });
    };
  }

  private state(base: string): BaseState {
    let st = this.states.get(base);
    if (!st) {
      st = { verified: false, lastProbeAt: -Infinity, lastResult: null, inFlight: null };
      this.states.set(base, st);
    }
    return st;
  }

  private async verify(base: string, port: number): Promise<ProbeResult> {
    const st = this.state(base);
    if (st.verified) return "authed";
    if (st.inFlight) return st.inFlight;
    if (st.lastResult !== null && this.deps.now() - st.lastProbeAt < REPROBE_INTERVAL_MS) {
      return st.lastResult;
    }
    st.lastProbeAt = this.deps.now();
    st.inFlight = this.deps.probe(port).finally(() => { st.inFlight = null; });
    const result = await st.inFlight;
    st.lastResult = result;
    st.verified = result === "authed";
    return result;
  }
}
