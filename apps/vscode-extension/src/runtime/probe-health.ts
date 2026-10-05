// vscode-free. GET /health?nonce= and verify the HMAC proofs (spec §3.4). Never sends
// the token: a client learns the server holds it without revealing it.
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export type ProbeResult =
  | "authed" | "other-backend" | "preauth" | "unauthorized" | "down" | "no-token";

export interface ProbeDeps {
  fetchRaw(url: string, init: { signal: AbortSignal }): Promise<{ status: number; body: string }>;
  readToken(port: number): string | undefined;
  nonce?: () => string;
}

export const PROBE_TIMEOUT_MS = 2000;

export function healthProof(token: string, nonce: string): string {
  return createHmac("sha256", token).update(`crucible-health-v1\0${nonce}`, "utf8").digest("hex");
}

export function healthBound(token: string, nonce: string, pid: number, workspace: string): string {
  return createHmac("sha256", token)
    .update(`crucible-health-bound-v1\0${nonce}\0${pid}\0${workspace}`, "utf8")
    .digest("hex");
}

function sameHex(a: unknown, b: string): boolean {
  if (typeof a !== "string" || a.length !== b.length) return false;
  return timingSafeEqual(Buffer.from(a), Buffer.from(b));
}

export async function probeHealth(
  port: number, deps: ProbeDeps, expect?: { pid: number; workspace: string },
): Promise<ProbeResult> {
  const nonce = deps.nonce?.() ?? randomBytes(16).toString("hex");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  let response: { status: number; body: string };
  try {
    response = await deps.fetchRaw(
      `http://127.0.0.1:${port}/health?nonce=${nonce}`, { signal: controller.signal });
  } catch {
    return "down";
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 503) return "down";
  const token = deps.readToken(port);
  if (token === undefined) return "no-token";
  if (response.status === 403 || response.status === 421) return "unauthorized";
  let body: { proof?: unknown; bound?: unknown; pid?: unknown };
  try {
    body = JSON.parse(response.body) as typeof body;
  } catch {
    return "preauth";
  }
  if (response.status !== 200 || body.proof === undefined) return "preauth";
  if (!sameHex(body.proof, healthProof(token, nonce))) return "unauthorized";
  if (expect) {
    if (body.pid !== expect.pid) return "other-backend";
    if (!sameHex(body.bound, healthBound(token, nonce, expect.pid, expect.workspace))) {
      return "other-backend";
    }
  }
  return "authed";
}
