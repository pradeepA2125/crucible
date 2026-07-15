import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
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
      gopls: { version: "v0.22.0", urls: { "darwin-arm64": "https://r/gopls" }, sha256: { "darwin-arm64": sha } },
      jre: { version: "17.0.19+10", urls: { "darwin-arm64": "https://r/jre" }, sha256: { "darwin-arm64": sha } },
      jdtls: { version: "1.61.0", urls: { any: "https://r/jdtls" }, sha256: { any: sha } },
      lsps: { version: "1", npmPackages: ["pyright@1.1.400", "typescript-language-server@4.3.3"] },
    },
  };
}

// Fakes the effect of a real extraction well enough for artifactPresent's
// findJavaExecutable/findEquinoxLauncher checks: write the marker file each
// looks for, keyed on destDir's basename since the fake has no other way to
// know which component it's extracting for.
async function fakeExtract(_archive: Buffer, destDir: string): Promise<void> {
  if (destDir.endsWith("jre")) {
    mkdirSync(join(destDir, "bin"), { recursive: true });
    writeFileSync(join(destDir, "bin", "java"), "");
  } else if (destDir.endsWith("jdtls")) {
    mkdirSync(join(destDir, "plugins"), { recursive: true });
    writeFileSync(join(destDir, "plugins", "org.eclipse.equinox.launcher_1.0.0.jar"), "");
  }
}

function deps(overrides: Partial<InstallerDeps> = {}): InstallerDeps & { calls: string[][] } {
  const calls: string[][] = [];
  return {
    runtimeDir: mkdtempSync(join(tmpdir(), "rt-")),
    manifest: manifest(),
    download: async () => BIN,
    exec: async (cmd, args) => { calls.push([cmd, ...args]); return { code: 0, stdout: "", stderr: "" }; },
    hasNode: async () => true,
    extract: fakeExtract,
    platform: "darwin-arm64",
    calls,
    ...overrides,
  };
}

describe("RuntimeInstaller", () => {
  it("happy path installs all nine components and writes runtime.json", async () => {
    const d = deps();
    const result = await new RuntimeInstaller(d).installAll();
    expect(result.ok).toBe(true);
    expect(result.components.map((c) => c.status)).toEqual(
      ["done", "done", "done", "done", "done", "done", "done", "done", "done"]);
    expect(existsSync(join(d.runtimeDir, "bin", "uv"))).toBe(true);
    expect(existsSync(join(d.runtimeDir, "bin", "rust-analyzer"))).toBe(true);
    expect(existsSync(join(d.runtimeDir, "bin", "gopls"))).toBe(true);
    expect(existsSync(join(d.runtimeDir, "jre", "bin", "java"))).toBe(true);
    expect(existsSync(join(d.runtimeDir, "jdtls", "plugins", "org.eclipse.equinox.launcher_1.0.0.jar"))).toBe(true);
    expect(d.calls.some(([c, a]) => c.endsWith("uv") && a === "venv")).toBe(true);
    const state = JSON.parse(readFileSync(join(d.runtimeDir, "runtime.json"), "utf8"));
    expect(state.releaseTag).toBe("v0.1.0");
  });

  it("jre install fails cleanly when the manifest has no artifact for this platform", async () => {
    const d = deps();
    d.manifest.components.jre = { version: "17.0.19+10" }; // no urls/sha256
    const result = await new RuntimeInstaller(d).installAll();
    const jre = result.components.find((c) => c.id === "jre")!;
    expect(jre.status).toBe("failed");
    expect(jre.detail).toMatch(/no darwin-arm64 artifact/i);
  });

  it("jdtls install fails cleanly when the manifest has no 'any' artifact", async () => {
    const d = deps();
    d.manifest.components.jdtls = { version: "1.61.0" }; // no urls/sha256
    const result = await new RuntimeInstaller(d).installAll();
    const jdtls = result.components.find((c) => c.id === "jdtls")!;
    expect(jdtls.status).toBe("failed");
    expect(jdtls.detail).toMatch(/no jdtls artifact/i);
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

  it("agentd install requests the [memory,semantic] extras via the bare-version fallback", async () => {
    const d = deps();
    await new RuntimeInstaller(d).installAll();
    const pipCall = d.calls.find((call) => call.includes("pip"));
    expect(pipCall).toBeDefined();
    expect(pipCall![pipCall!.length - 1]).toBe("crucible-agentd[memory,semantic]==0.1.0");
  });

  it("agentd install wraps a manifest wheel URL with the [memory,semantic] extras as a PEP 508 direct reference", async () => {
    const d = deps();
    d.manifest.components.agentd = { version: "0.3.0", urls: { any: "https://example.com/pkg.whl" } };
    await new RuntimeInstaller(d).installAll();
    const pipCall = d.calls.find((call) => call.includes("pip"));
    expect(pipCall).toBeDefined();
    expect(pipCall![pipCall!.length - 1]).toBe(
      "crucible-agentd[memory,semantic] @ https://example.com/pkg.whl");
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

  it("same version string but different artifact content still triggers reinstall (content-hash staleness key)", async () => {
    // Reproduces the crucible-agentd 0.2.0/0.2.1 incident: a release published new
    // artifact bytes under an unchanged version label. install-state.json only ever
    // records what stalenessKey() computed, so simulate a prior install recorded
    // under the OLD content's hash, then swap the manifest's bytes (and therefore
    // its sha256) while leaving "version" untouched — the installer must not treat
    // this as already installed.
    const d = deps();
    await new RuntimeInstaller(d).installAll();

    const staleVersion = d.manifest.components.indexer.version;
    const newBin = Buffer.from("#!/bin/sh\necho updated\n");
    const newSha = sha256Hex(newBin);
    expect(newSha).not.toBe(sha256Hex(BIN)); // sanity: genuinely different content

    let downloadedIndexer = false;
    const d2: InstallerDeps = {
      ...d,
      manifest: {
        ...d.manifest,
        components: {
          ...d.manifest.components,
          // same version string as before — only the bytes/hash changed
          indexer: { version: staleVersion, urls: { "darwin-arm64": "https://r/ix" }, sha256: { "darwin-arm64": newSha } },
        },
      },
      download: async (url: string) => {
        if (url.endsWith("/ix")) downloadedIndexer = true;
        return url.endsWith("/ix") ? newBin : BIN;
      },
    };

    const result = await new RuntimeInstaller(d2).installAll();
    const indexer = result.components.find((c) => c.id === "indexer")!;
    expect(downloadedIndexer).toBe(true);
    expect(indexer.status).toBe("done");
    expect(indexer.detail).not.toBe("already installed");
  });

  it("a hollow venv (state recorded, python binary present, package not importable) is reinstalled, not silently marked done", async () => {
    const d = deps();
    // Simulate a prior interrupted install: install-state.json + a venv/bin/python
    // exist on disk (the two things the old check looked for), but nothing is
    // actually importable inside that venv — the pip install never completed.
    writeFileSync(join(d.runtimeDir, "install-state.json"), JSON.stringify({ agentd: "0.1.0" }));
    const pyPath = venvPython(d.runtimeDir, d.platform);
    mkdirSync(dirname(pyPath), { recursive: true });
    writeFileSync(pyPath, "");

    let importCheckRan = false;
    const d2: InstallerDeps & { calls: string[][] } = {
      ...d,
      exec: async (cmd, args) => {
        d.calls.push([cmd, ...args]);
        if (cmd === pyPath && args.includes("import uvicorn")) {
          importCheckRan = true;
          return { code: 1, stdout: "", stderr: "ModuleNotFoundError: No module named 'uvicorn'" };
        }
        return { code: 0, stdout: "", stderr: "" };
      },
    };

    const result = await new RuntimeInstaller(d2).installAll();
    const agentd = result.components.find((c) => c.id === "agentd")!;
    expect(importCheckRan).toBe(true);
    expect(agentd.status).toBe("done"); // reinstall attempted and (per the mock) succeeded
    expect(agentd.detail).not.toBe("already installed");
    const pipCall = d2.calls.find((call) => call.includes("pip"));
    expect(pipCall).toBeDefined(); // the real pip install actually ran this time
  });

  it("a genuinely working venv (import succeeds) is still treated as already installed", async () => {
    const d = deps();
    writeFileSync(join(d.runtimeDir, "install-state.json"), JSON.stringify({ agentd: "0.1.0" }));
    const pyPath = venvPython(d.runtimeDir, d.platform);
    mkdirSync(dirname(pyPath), { recursive: true });
    writeFileSync(pyPath, "");

    const d2: InstallerDeps & { calls: string[][] } = {
      ...d,
      exec: async (cmd, args) => {
        d.calls.push([cmd, ...args]);
        return { code: 0, stdout: "", stderr: "" }; // import check + everything else succeeds
      },
    };

    await new RuntimeInstaller(d2).installAll();
    const pipCall = d2.calls.find((call) => call.includes("pip"));
    expect(pipCall).toBeUndefined(); // no reinstall needed
  });
});

describe("venvPython", () => {
  it("posix and windows layouts", () => {
    expect(venvPython("/r", "darwin-arm64")).toBe("/r/venv/bin/python");
    expect(venvPython("/r", "win32-x64")).toContain(join("venv", "Scripts", "python.exe"));
  });
});
