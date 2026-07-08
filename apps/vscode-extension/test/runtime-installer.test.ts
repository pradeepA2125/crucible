import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { RuntimeInstaller, venvPython, type InstallerDeps } from "../src/runtime/installer.js";
import { sha256Hex, type RuntimeManifest } from "../src/runtime/manifest.js";

const BIN = Buffer.from("#!/bin/sh\necho hi\n");

function manifest(): RuntimeManifest {
  const sha = sha256Hex(BIN);
  return {
    manifestVersion: 1,
    releaseTag: "v0.1.0",
    components: {
      uv: { version: "0.5.0", urls: { "darwin-arm64": "https://r/uv" }, sha256: { "darwin-arm64": sha } },
      agentd: { version: "0.1.0" },
      indexer: { version: "0.1.0", urls: { "darwin-arm64": "https://r/ix" }, sha256: { "darwin-arm64": sha } },
      ripgrep: { version: "14.1.0", urls: { "darwin-arm64": "https://r/rg" }, sha256: { "darwin-arm64": sha } },
      "rust-analyzer": { version: "2026-07-06", urls: { "darwin-arm64": "https://r/ra" }, sha256: { "darwin-arm64": sha } },
      lsps: { version: "1", npmPackages: ["pyright@1.1.400", "typescript-language-server@4.3.3"] },
    },
  };
}

function deps(overrides: Partial<InstallerDeps> = {}): InstallerDeps & { calls: string[][] } {
  const calls: string[][] = [];
  return {
    runtimeDir: mkdtempSync(join(tmpdir(), "rt-")),
    manifest: manifest(),
    download: async () => BIN,
    exec: async (cmd, args) => { calls.push([cmd, ...args]); return { code: 0, stdout: "", stderr: "" }; },
    hasNode: async () => true,
    platform: "darwin-arm64",
    calls,
    ...overrides,
  };
}

describe("RuntimeInstaller", () => {
  it("happy path installs all six components and writes runtime.json", async () => {
    const d = deps();
    const result = await new RuntimeInstaller(d).installAll();
    expect(result.ok).toBe(true);
    expect(result.components.map((c) => c.status)).toEqual(
      ["done", "done", "done", "done", "done", "done"]);
    expect(existsSync(join(d.runtimeDir, "bin", "uv"))).toBe(true);
    expect(existsSync(join(d.runtimeDir, "bin", "rust-analyzer"))).toBe(true);
    expect(d.calls.some(([c, a]) => c.endsWith("uv") && a === "venv")).toBe(true);
    const state = JSON.parse(readFileSync(join(d.runtimeDir, "runtime.json"), "utf8"));
    expect(state.releaseTag).toBe("v0.1.0");
  });

  it("checksum mismatch fails that component, uv failure cascades to agentd only", async () => {
    const d = deps({ download: async (url) => url.endsWith("uv") ? Buffer.from("evil") : BIN });
    const result = await new RuntimeInstaller(d).installAll();
    const byId = Object.fromEntries(result.components.map((c) => [c.id, c]));
    expect(result.ok).toBe(false);
    expect(byId.uv.status).toBe("failed");
    expect(byId.uv.detail).toMatch(/checksum/i);
    expect(byId.agentd.status).toBe("failed");
    expect(byId.indexer.status).toBe("done"); // independent components still run
  });

  it("node absent skips lsps with a degraded-consequence detail", async () => {
    const d = deps({ hasNode: async () => false });
    const result = await new RuntimeInstaller(d).installAll();
    const lsps = result.components.find((c) => c.id === "lsps")!;
    expect(result.ok).toBe(true);
    expect(lsps.status).toBe("skipped");
    expect(lsps.detail).toMatch(/degraded/i);
  });

  it("agentd install requests the [memory] extra via the bare-version fallback", async () => {
    const d = deps();
    await new RuntimeInstaller(d).installAll();
    const pipCall = d.calls.find((call) => call.includes("pip"));
    expect(pipCall).toBeDefined();
    expect(pipCall![pipCall!.length - 1]).toBe("ai-editor-agentd[memory]==0.1.0");
  });

  it("agentd install wraps a manifest wheel URL with the [memory] extra as a PEP 508 direct reference", async () => {
    const d = deps();
    d.manifest.components.agentd = { version: "0.3.0", urls: { any: "https://example.com/pkg.whl" } };
    await new RuntimeInstaller(d).installAll();
    const pipCall = d.calls.find((call) => call.includes("pip"));
    expect(pipCall).toBeDefined();
    expect(pipCall![pipCall!.length - 1]).toBe(
      "ai-editor-agentd[memory] @ https://example.com/pkg.whl");
  });

  it("resume: matching install-state version skips the download", async () => {
    const d = deps();
    await new RuntimeInstaller(d).installAll();
    let downloads = 0;
    const d2 = { ...d, download: async () => { downloads++; return BIN; } };
    const result = await new RuntimeInstaller(d2).installAll();
    expect(result.ok).toBe(true);
    expect(downloads).toBe(0);
  });
});

describe("venvPython", () => {
  it("posix and windows layouts", () => {
    expect(venvPython("/r", "darwin-arm64")).toBe("/r/venv/bin/python");
    expect(venvPython("/r", "win32-x64")).toContain(join("venv", "Scripts", "python.exe"));
  });
});
