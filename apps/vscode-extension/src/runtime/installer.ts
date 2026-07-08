// vscode-free: all effects behind InstallerDeps so tests inject fakes.
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  platformKey,
  verifyChecksum,
  type ComponentId,
  type PlatformKey,
  type RuntimeManifest,
} from "./manifest.js";

export interface ExecResult { code: number; stdout: string; stderr: string }
export interface InstallerDeps {
  runtimeDir: string;
  manifest: RuntimeManifest;
  download(url: string): Promise<Buffer>;
  exec(cmd: string, args: string[], opts?: { cwd?: string }): Promise<ExecResult>;
  hasNode(): Promise<boolean>;
  platform?: PlatformKey;               // default platformKey()
}
export type ComponentStatus = "pending" | "running" | "done" | "failed" | "skipped";
export interface ComponentProgress { id: ComponentId; status: ComponentStatus; detail?: string }
export interface InstallResult { ok: boolean; components: ComponentProgress[] }

// Order matters: agentd needs uv on disk first.
const ORDER: ComponentId[] = ["uv", "agentd", "indexer", "ripgrep", "rust-analyzer", "lsps"];
const BIN_NAME: Partial<Record<ComponentId, string>> = {
  uv: "uv", indexer: "crucible-indexer", ripgrep: "rg", "rust-analyzer": "rust-analyzer",
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
        } else if (state[id] === spec.version && this.artifactPresent(id)) {
          progress = { id, status: "done", detail: "already installed" };
        } else {
          progress = await this.installOne(id);
          if (progress.status === "done") {
            state[id] = spec.version;
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

  private artifactPresent(id: ComponentId): boolean {
    const bin = BIN_NAME[id];
    if (bin) return existsSync(binPath(this.deps.runtimeDir, bin, this.platform));
    if (id === "agentd") return existsSync(venvPython(this.deps.runtimeDir, this.platform));
    return existsSync(join(this.deps.runtimeDir, "node_modules"));
  }

  private async installOne(id: ComponentId): Promise<ComponentProgress> {
    const spec = this.deps.manifest.components[id];
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
      // instead of silently degrading its embedder. PEP 508 direct-reference syntax
      // ("name[extra] @ url") is required to combine an extras marker with a URL install.
      const target = spec.urls?.any
        ? `crucible-agentd[memory] @ ${spec.urls.any}`
        : `crucible-agentd[memory]==${spec.version}`;
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
