// vscode-free: constructs the jdtls launch command from an extracted JRE +
// jdtls install tree. Reference logic ported from the jdtls project's own
// Python launcher (github.com/eclipse-jdtls/eclipse.jdt.ls, bin/jdtls.py) —
// verified directly against a real downloaded jdtls snapshot archive and a
// locally-installed jdtls (Homebrew formula) before writing this.
import { createHash } from "node:crypto";
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import type { PlatformKey } from "./manifest.js";

/// jdtls splits its shared OSGi config by OS AND, as of recent snapshots,
/// by architecture too (config_mac_arm / config_linux_arm alongside the
/// older config_mac / config_linux / config_win) — verified against a real
/// downloaded archive. Prefer the arch-specific dir; fall back to the
/// arch-generic one for older jdtls builds that don't split by arch (e.g.
/// win32-x64 only ever had one variant in every version inspected).
const CONFIG_DIR_CANDIDATES: Record<PlatformKey, string[]> = {
  "darwin-arm64": ["config_mac_arm", "config_mac"],
  "darwin-x64": ["config_mac"],
  "linux-x64": ["config_linux"],
  "win32-x64": ["config_win"],
};

export function configDirForPlatform(jdtlsDir: string, platform: PlatformKey): string | null {
  for (const candidate of CONFIG_DIR_CANDIDATES[platform]) {
    const path = join(jdtlsDir, candidate);
    if (existsSync(path)) return path;
  }
  return null;
}

/// The main launcher jar is `org.eclipse.equinox.launcher_<version>.jar` —
/// an underscore immediately after "launcher". Platform-specific NATIVE
/// FRAGMENT jars (`org.eclipse.equinox.launcher.cocoa.macosx.aarch64_*.jar`,
/// `...gtk.linux.x86_64_*.jar`, `...win32.win32.x86_64_*.jar`) have a `.`
/// immediately after "launcher" instead and are resolved by the OSGi
/// framework at runtime — they must NOT be picked here. The exact version
/// suffix isn't pinned by us (it changes every jdtls release), so this is a
/// glob, matching the jdtls project's own launcher script rather than a
/// hardcoded filename.
export function findEquinoxLauncher(jdtlsDir: string): string | null {
  const pluginsDir = join(jdtlsDir, "plugins");
  if (!existsSync(pluginsDir)) return null;
  for (const name of readdirSync(pluginsDir)) {
    if (/^org\.eclipse\.equinox\.launcher_.*\.jar$/.test(name)) {
      return join(pluginsDir, name);
    }
  }
  return null;
}

/// Search up to `maxDepth` directories deep for `bin/java[.exe]` under an
/// extracted JRE archive root. Adoptium archives nest everything under one
/// version-specific top-level directory (e.g. `jdk-17.0.19+10-jre/`), so we
/// discover the path rather than hardcode it — the exact name changes every
/// patch release we'd otherwise have to keep in sync.
export function findJavaExecutable(
  jreDir: string,
  platform: PlatformKey,
  maxDepth = 3,
): string | null {
  const exeName = platform === "win32-x64" ? "java.exe" : "java";
  const direct = join(jreDir, "bin", exeName);
  if (existsSync(direct)) return direct;
  if (maxDepth <= 0 || !existsSync(jreDir)) return null;

  for (const entry of readdirSync(jreDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const found = findJavaExecutable(join(jreDir, entry.name), platform, maxDepth - 1);
    if (found) return found;
  }
  return null;
}

/// Workspace-scoped data directory jdtls requires (`-data`): reusing one
/// directory across unrelated projects corrupts jdtls's index state. The
/// upstream launcher hashes only `basename(cwd)`, which collides for two
/// differently-located projects that happen to share a folder name; hashing
/// the full resolved workspace path avoids that.
export function jdtlsDataDir(runtimeDir: string, workspacePath: string): string {
  const hash = createHash("sha1").update(workspacePath).digest("hex");
  return join(runtimeDir, "jdtls-data", hash);
}

/// Quote an argument for `indexer-rs`'s `parse_command` tokenizer
/// (`services/indexer-rs/src/lsp.rs`) — a simple whitespace splitter that
/// understands single/double quotes but no escaping. Wrapping in double
/// quotes is sufficient for filesystem paths (never contain `"`).
function quoteArg(arg: string): string {
  return /\s/.test(arg) ? `"${arg}"` : arg;
}

export interface JdtlsCommandInput {
  javaExecutable: string;
  launcherJar: string;
  configDir: string;
  dataDir: string;
}

/// Build the full `CRUCIBLE_LSP_JAVA_CMD` string. JVM args ported verbatim
/// from the jdtls project's reference launcher (see file header) — the
/// `osgi.sharedConfiguration.area` pair is what selects the platform config
/// dir; `-data` is the workspace-scoped index/state directory.
export function buildJdtlsCommand(input: JdtlsCommandInput): string {
  const args = [
    quoteArg(input.javaExecutable),
    "-Declipse.application=org.eclipse.jdt.ls.core.id1",
    "-Dosgi.bundles.defaultStartLevel=4",
    "-Declipse.product=org.eclipse.jdt.ls.core.product",
    "-Dosgi.checkConfiguration=true",
    `-Dosgi.sharedConfiguration.area=${quoteArg(input.configDir)}`,
    "-Dosgi.sharedConfiguration.area.readOnly=true",
    "-Dosgi.configuration.cascaded=true",
    "-Xms1G",
    "--add-modules=ALL-SYSTEM",
    "--add-opens",
    "java.base/java.util=ALL-UNNAMED",
    "--add-opens",
    "java.base/java.lang=ALL-UNNAMED",
    "-jar",
    quoteArg(input.launcherJar),
    "-data",
    quoteArg(input.dataDir),
  ];
  return args.join(" ");
}
