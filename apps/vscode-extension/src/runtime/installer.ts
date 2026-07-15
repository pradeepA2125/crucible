// vscode-free: all effects behind InstallerDeps so tests inject fakes.
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { findEquinoxLauncher, findJavaExecutable } from "./jdtls.js";
import {
  platformKey,
  verifyChecksum,
  type ComponentId,
  type ComponentSpec,
  type PlatformKey,
  type RuntimeManifest,
} from "./manifest.js";

export interface ExecResult { code: number; stdout: string; stderr: string }
export type ArchiveFormat = "tar.gz" | "zip";
export interface InstallerDeps {
  runtimeDir: string;
  manifest: RuntimeManifest;
  download(url: string): Promise<Buffer>;
  exec(cmd: string, args: string[], opts?: { cwd?: string }): Promise<ExecResult>;
  hasNode(): Promise<boolean>;
  // jre/jdtls are directory-tree archives, unlike every other component
  // (a single self-contained binary written straight to bin/) — this
  // extracts `archive` into `destDir`.
  extract(archive: Buffer, destDir: string, format: ArchiveFormat): Promise<void>;
  platform?: PlatformKey;               // default platformKey()
}
export type ComponentStatus = "pending" | "running" | "done" | "failed" | "skipped";
export interface ComponentProgress { id: ComponentId; status: ComponentStatus; detail?: string }
export interface InstallResult { ok: boolean; components: ComponentProgress[] }

// Order matters: agentd needs uv on disk first.
const ORDER: ComponentId[] = [
  "uv", "agentd", "indexer", "ripgrep", "rust-analyzer", "gopls", "jre", "jdtls", "lsps",
];
const BIN_NAME: Partial<Record<ComponentId, string>> = {
  uv: "uv", indexer: "crucible-indexer", ripgrep: "rg", "rust-analyzer": "rust-analyzer", gopls: "gopls",
};

export function binPath(runtimeDir: string, name: string, platform: PlatformKey = platformKey()): string {
  const exe = platform === "win32-x64" ? `${name}.exe` : name;
  return join(runtimeDir, "bin", exe);
}

export function venvPython(runtimeDir: string, platform: PlatformKey = platformKey()): string {
  return platform === "win32-x64"
    ? join(runtimeDir, "venv", "Scripts", "python.exe")
    : join(runtimeDir, "venv", "bin", "python");
}

function readState(runtimeDir: string): Partial<Record<ComponentId, string>> {
  try {
    return JSON.parse(readFileSync(join(runtimeDir, "install-state.json"), "utf8"));
  } catch {
    return {};
  }
}

function writeState(runtimeDir: string, state: Partial<Record<ComponentId, string>>): void {
  writeFileSync(join(runtimeDir, "install-state.json"), JSON.stringify(state, null, 2));
}

// The "already installed, skip" decision keys off this, not spec.version directly.
// A manifest's sha256 is computed from the actual released artifact bytes, so it
// changes exactly when the artifact changes — unlike a hand-maintained version
// field, which can go stale (crucible-agentd's pyproject.toml stayed "0.2.0" across
// a release that changed its source, so a version-string comparison silently kept
// an old wheel installed). Falls back to spec.version only when there's no
// downloadable artifact to hash: agentd's bare-version PyPI-fallback path (no
// urls/sha256 in the manifest) and lsps (an npm install, not a hashed download —
// its "version" is already a content hash of the pinned package list, see
// make_manifest.py).
function stalenessKey(spec: ComponentSpec, platform: PlatformKey): string {
  return spec.sha256?.[platform] ?? spec.sha256?.any ?? spec.version;
}

export class RuntimeInstaller {
  private readonly platform: PlatformKey;

  constructor(private readonly deps: InstallerDeps) {
    this.platform = deps.platform ?? platformKey();
  }

  async installAll(onProgress?: (p: ComponentProgress) => void): Promise<InstallResult> {
    mkdirSync(join(this.deps.runtimeDir, "bin"), { recursive: true });
    const state = readState(this.deps.runtimeDir);
    const results: ComponentProgress[] = [];
    let uvOk = true;

    for (const id of ORDER) {
      const spec = this.deps.manifest.components[id];
      const emit = (p: ComponentProgress) => { onProgress?.(p); };
      emit({ id, status: "running" });
      let progress: ComponentProgress;
      try {
        if (id === "agentd" && !uvOk) {
          progress = { id, status: "failed", detail: "uv unavailable" };
        } else if (
          state[id] === stalenessKey(spec, this.platform) && (await this.artifactPresent(id))
        ) {
          progress = { id, status: "done", detail: "already installed" };
        } else {
          progress = await this.installOne(id);
          if (progress.status === "done") {
            state[id] = stalenessKey(spec, this.platform);
            writeState(this.deps.runtimeDir, state);
          }
        }
      } catch (err) {
        progress = { id, status: "failed", detail: err instanceof Error ? err.message : String(err) };
      }
      if (id === "uv" && progress.status !== "done") uvOk = false;
      emit(progress);
      results.push(progress);
    }

    const ok = results.every((c) => c.status !== "failed");
    if (ok) {
      const versions = Object.fromEntries(
        ORDER.map((id) => [id, this.deps.manifest.components[id].version]));
      writeFileSync(join(this.deps.runtimeDir, "runtime.json"), JSON.stringify(
        { releaseTag: this.deps.manifest.releaseTag, components: versions }, null, 2));
    }
    return { ok, components: results };
  }

  private async artifactPresent(id: ComponentId): Promise<boolean> {
    const bin = BIN_NAME[id];
    if (bin) return existsSync(binPath(this.deps.runtimeDir, bin, this.platform));
    if (id === "jre") {
      return findJavaExecutable(join(this.deps.runtimeDir, "jre"), this.platform) !== null;
    }
    if (id === "jdtls") {
      return findEquinoxLauncher(join(this.deps.runtimeDir, "jdtls")) !== null;
    }
    if (id === "agentd") {
      const py = venvPython(this.deps.runtimeDir, this.platform);
      if (!existsSync(py)) return false;
      // A prior install can leave the venv shell + a stale install-state.json
      // entry behind without ever completing `uv pip install` (interrupted
      // network, laptop sleep, ...). Confirm the package actually imports
      // before trusting the recorded version — otherwise a hollow venv looks
      // "already installed" forever and the backend fails at startup with
      // "No module named uvicorn".
      const check = await this.deps.exec(py, ["-c", "import uvicorn"]);
      return check.code === 0;
    }
    return existsSync(join(this.deps.runtimeDir, "node_modules"));
  }

  private async installOne(id: ComponentId): Promise<ComponentProgress> {
    const spec = this.deps.manifest.components[id];
    if (id === "jre") {
      const url = spec.urls?.[this.platform];
      const sha = spec.sha256?.[this.platform];
      if (!url || !sha) throw new Error(`manifest has no ${this.platform} artifact for jre`);
      const data = await this.deps.download(url);
      verifyChecksum(data, sha);
      const destDir = join(this.deps.runtimeDir, "jre");
      mkdirSync(destDir, { recursive: true });
      await this.deps.extract(data, destDir, this.platform === "win32-x64" ? "zip" : "tar.gz");
      return { id, status: "done" };
    }
    if (id === "jdtls") {
      // Platform-independent: one universal archive under the "any" key
      // (see manifest.ts's ComponentSpec doc comment).
      const url = spec.urls?.any;
      const sha = spec.sha256?.any;
      if (!url || !sha) throw new Error("manifest has no jdtls artifact");
      const data = await this.deps.download(url);
      verifyChecksum(data, sha);
      const destDir = join(this.deps.runtimeDir, "jdtls");
      mkdirSync(destDir, { recursive: true });
      await this.deps.extract(data, destDir, "tar.gz");
      return { id, status: "done" };
    }
    if (id === "lsps") {
      if (!(await this.deps.hasNode())) {
        return { id, status: "skipped", detail: "Node.js not found — code-graph edges degraded" };
      }
      const res = await this.deps.exec(
        "npm", ["install", "--prefix", this.deps.runtimeDir, ...(spec.npmPackages ?? [])]);
      if (res.code !== 0) throw new Error(`npm install failed: ${res.stderr.slice(0, 400)}`);
      return { id, status: "done" };
    }
    if (id === "agentd") {
      const uv = binPath(this.deps.runtimeDir, "uv", this.platform);
      const venv = await this.deps.exec(uv, ["venv", join(this.deps.runtimeDir, "venv"), "--python", "3.12"]);
      if (venv.code !== 0) throw new Error(`uv venv failed: ${venv.stderr.slice(0, 400)}`);
      // [memory] pulls in sentence-transformers/numpy (and PyTorch, transitively) so the
      // memory harness (on by default — see agentd/memory/config.py) works out of the box
      // instead of silently degrading its embedder. [semantic] adds lancedb so semantic
      // retrieval (CRUCIBLE_SEMANTIC_RETRIEVAL, on by default via start-backend.sh) also
      // works out of the box rather than degrading to graph-only retrieval — found live:
      // without it, every index build logged "lancedb is required for semantic retrieval."
      // PEP 508 direct-reference syntax ("name[extras] @ url") is required to combine an
      // extras marker with a URL install.
      const target = spec.urls?.any
        ? `crucible-agentd[memory,semantic] @ ${spec.urls.any}`
        : `crucible-agentd[memory,semantic]==${spec.version}`;
      const pip = await this.deps.exec(
        uv, ["pip", "install", "--python", venvPython(this.deps.runtimeDir, this.platform), target]);
      if (pip.code !== 0) throw new Error(`uv pip install failed: ${pip.stderr.slice(0, 400)}`);
      return { id, status: "done" };
    }
    // binary components: uv / indexer / ripgrep
    const url = spec.urls?.[this.platform];
    const sha = spec.sha256?.[this.platform];
    if (!url || !sha) throw new Error(`manifest has no ${this.platform} artifact for ${id}`);
    const data = await this.deps.download(url);
    verifyChecksum(data, sha);
    const dest = binPath(this.deps.runtimeDir, BIN_NAME[id]!, this.platform);
    writeFileSync(dest, data);
    if (this.platform !== "win32-x64") chmodSync(dest, 0o755);
    return { id, status: "done" };
  }
}
