import { describe, expect, it, vi } from "vitest";
import { healthBound, healthProof, probeHealth, type ProbeDeps } from "../src/runtime/probe-health.js";

const TOKEN = "k".repeat(43);
const NONCE = "00112233445566778899aabbccddeeff";

// `null` = no token file. (An explicit `undefined` would trigger the default instead.)
function deps(respond: (url: string) => { status: number; body: string } | Error,
              token: string | null = TOKEN): ProbeDeps & { urls: string[] } {
  const urls: string[] = [];
  return {
    urls,
    nonce: () => NONCE,
    readToken: () => token ?? undefined,
    fetchRaw: vi.fn(async (url: string) => {
      urls.push(url);
      const r = respond(url);
      if (r instanceof Error) throw r;
      return r;
    }),
  };
}

const authedBody = (pid = 7, ws = "/w") => JSON.stringify({
  status: "ok", pid, proof: healthProof(TOKEN, NONCE), bound: healthBound(TOKEN, NONCE, pid, ws) });

describe("probeHealth", () => {
  it("is authed for a valid proof and probes 127.0.0.1 without a token", async () => {
    const d = deps(() => ({ status: 200, body: authedBody() }));
    expect(await probeHealth(9, d)).toBe("authed");
    expect(d.urls[0]).toBe(`http://127.0.0.1:9/health?nonce=${NONCE}`);
    expect((d.fetchRaw as ReturnType<typeof vi.fn>).mock.calls[0][1]).not.toHaveProperty("headers");
  });
  it("checks identity when expect is given", async () => {
    const d = deps(() => ({ status: 200, body: authedBody(7, "/w") }));
    expect(await probeHealth(9, d, { pid: 7, workspace: "/w" })).toBe("authed");
    expect(await probeHealth(9, d, { pid: 8, workspace: "/w" })).toBe("other-backend");
    expect(await probeHealth(9, d, { pid: 7, workspace: "/other" })).toBe("other-backend");
  });
  it("classifies the rest", async () => {
    expect(await probeHealth(9, deps(() => ({ status: 200, body: '{"status":"ok"}' })))).toBe("preauth");
    expect(await probeHealth(9, deps(() => ({ status: 421, body: "" })))).toBe("unauthorized");
    expect(await probeHealth(9, deps(() => ({ status: 403, body: "" })))).toBe("unauthorized");
    const wrong = JSON.stringify({ status: "ok", pid: 7, proof: "00", bound: "00" });
    expect(await probeHealth(9, deps(() => ({ status: 200, body: wrong })))).toBe("unauthorized");
    expect(await probeHealth(9, deps(() => new TypeError("fetch failed")))).toBe("down");
    expect(await probeHealth(9, deps(() => ({ status: 503, body: "" })))).toBe("down");
    expect(await probeHealth(9, deps(() => ({ status: 200, body: authedBody() }), null)))
      .toBe("no-token");
  });
  it("gives up after 2 s", async () => {
    vi.useFakeTimers();
    const d: ProbeDeps = { readToken: () => TOKEN, nonce: () => NONCE,
      fetchRaw: (_url, init) => new Promise((_res, rej) =>
        init.signal.addEventListener("abort", () => rej(new Error("aborted")))) };
    const pending = probeHealth(9, d);
    await vi.advanceTimersByTimeAsync(2001);
    expect(await pending).toBe("down");
    vi.useRealTimers();
  });
});
