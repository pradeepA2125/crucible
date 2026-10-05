import { describe, expect, it, vi } from "vitest";
import { BackendAuthError } from "@crucible/editor-client";
import { BackendGate, normalizeBackendUrl, type GateDeps } from "../src/backend-auth/backend-gate.js";
import type { ProbeResult } from "../src/runtime/probe-health.js";

function gate(results: ProbeResult[], over: Partial<GateDeps> = {}) {
  let t = 0;
  const probe = vi.fn(async () => results.shift() ?? "authed");
  const fetch = vi.fn(async () => new Response("{}", { status: 200 }));
  const report = vi.fn();
  const g = new BackendGate({ fetch, probe, readToken: () => "T", report,
    now: () => t, ...over });
  return { g, probe, fetch, report, advance: (ms: number) => { t += ms; } };
}

describe("normalizeBackendUrl", () => {
  it("rewrites localhost before probing", () => {
    expect(normalizeBackendUrl("http://localhost:8000/")).toEqual(
      { base: "http://127.0.0.1:8000", port: 8000, isLoopback: true });
    expect(normalizeBackendUrl("http://example.com:8000").isLoopback).toBe(false);
  });
});

describe("BackendGate", () => {
  it("probes before the first request and forwards once authed", async () => {
    const { g, probe, fetch } = gate(["authed"]);
    await g.fetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/v1/config");
    await g.fetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/v1/config");
    expect(probe).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it.each(["unauthorized", "preauth", "no-token", "other-backend"] as const)(
    "%s: sends nothing, reports, throws BackendAuthError", async (result) => {
      const { g, fetch, report } = gate([result]);
      await expect(g.fetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/x"))
        .rejects.toBeInstanceOf(BackendAuthError);
      expect(fetch).not.toHaveBeenCalled();
      expect(report).toHaveBeenCalledWith("http://127.0.0.1:9", false, expect.any(String));
    });

  it("down: sends nothing and reports nothing", async () => {
    const { g, fetch, report } = gate(["down"]);
    await expect(g.fetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/x")).rejects.toThrow(TypeError);
    expect(fetch).not.toHaveBeenCalled();
    expect(report).not.toHaveBeenCalled();
  });

  it("re-probes after a connection error, rate-limited", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response("{}", { status: 200 }))
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValue(new Response("{}", { status: 200 }));
    const { g, probe, advance } = gate(["authed", "down", "down", "authed"], { fetch });
    const f = g.fetchFor("http://127.0.0.1:9");
    await f("http://127.0.0.1:9/a");
    await expect(f("http://127.0.0.1:9/a")).rejects.toThrow(); // connection error
    await expect(f("http://127.0.0.1:9/a")).rejects.toThrow(); // probe → down
    await expect(f("http://127.0.0.1:9/a")).rejects.toThrow(); // within 5 s: no probe
    expect(probe).toHaveBeenCalledTimes(2);
    advance(5001);
    await expect(f("http://127.0.0.1:9/a")).rejects.toThrow(); // probe → down
    advance(5001);
    await f("http://127.0.0.1:9/a");                           // probe → authed
    expect(probe).toHaveBeenCalledTimes(4);
  });

  it("shares one in-flight probe between concurrent requests", async () => {
    const { g, probe } = gate(["authed"]);
    const f = g.fetchFor("http://127.0.0.1:9");
    await Promise.all([f("http://127.0.0.1:9/a"), f("http://127.0.0.1:9/b")]);
    expect(probe).toHaveBeenCalledTimes(1);
  });

  it("never offers a token for a non-loopback base", async () => {
    const { g, fetch } = gate(["authed"]);
    expect(g.tokenFor("http://example.com:9")()).toBeUndefined();
    await expect(g.fetchFor("http://example.com:9")("http://example.com:9/x"))
      .rejects.toBeInstanceOf(BackendAuthError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("authedFetchFor attaches the header after verifying", async () => {
    const { g, fetch } = gate(["authed"]);
    await g.authedFetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/v1/index/build", { method: "POST" });
    const init = fetch.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer T");
    expect(init.redirect).toBe("manual");
  });
});
