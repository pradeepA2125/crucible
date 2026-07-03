// vscode-free: node crypto only. Types are the contract with scripts/release/make_manifest.py.
import { createHash } from "node:crypto";

export type PlatformKey = "darwin-arm64" | "darwin-x64" | "linux-x64" | "win32-x64";
export type ComponentId = "uv" | "agentd" | "indexer" | "ripgrep" | "lsps";

export interface ComponentSpec {
  version: string;
  // binary components: per-platform url+sha256. agentd: single wheel url+sha256
  // under the "any" key. lsps: npm package specs, no url.
  urls?: Partial<Record<PlatformKey | "any", string>>;
  sha256?: Partial<Record<PlatformKey | "any", string>>;
  npmPackages?: string[];
}

export interface RuntimeManifest {
  manifestVersion: 1;
  releaseTag: string;
  components: Record<ComponentId, ComponentSpec>;
}

const SUPPORTED: Record<string, PlatformKey> = {
  "darwin-arm64": "darwin-arm64",
  "darwin-x64": "darwin-x64",
  "linux-x64": "linux-x64",
  "win32-x64": "win32-x64",
};

export function platformKey(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
): PlatformKey {
  const key = SUPPORTED[`${platform}-${arch}`];
  if (!key) throw new Error(`unsupported platform: ${platform}-${arch}`);
  return key;
}

export function sha256Hex(data: Buffer): string {
  return createHash("sha256").update(data).digest("hex");
}

export function verifyChecksum(data: Buffer, expectedHex: string): void {
  const actual = sha256Hex(data);
  if (actual !== expectedHex.toLowerCase()) {
    throw new Error(`checksum mismatch: expected ${expectedHex}, got ${actual}`);
  }
}
