// vscode-free. No request — and no token — goes to a backend URL until /health has
// proven the server there holds the token (spec §3.5 "Verify before sending").
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
      if (!target.isLoopback) {
        const reason = "the backend must be at 127.0.0.1";
        this.deps.report(target.base, false, reason);
        throw new BackendAuthError(reason, null);
      }
      const result = await this.verify(target.base, target.port);
      if (result === "down") {
        throw new TypeError(`fetch failed: backend at ${target.base} is not reachable`);
      }
      if (result !== "authed") {
        this.deps.report(target.base, false, REASONS[result]);
        throw new BackendAuthError(REASONS[result], null);
      }
      try {
        return await this.deps.fetch(input, init);
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
