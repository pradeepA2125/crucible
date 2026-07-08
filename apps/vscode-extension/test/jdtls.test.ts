import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  buildJdtlsCommand,
  configDirForPlatform,
  findEquinoxLauncher,
  findJavaExecutable,
  jdtlsDataDir,
} from "../src/runtime/jdtls.js";

function tmp(): string {
  return mkdtempSync(join(tmpdir(), "jdtls-test-"));
}

describe("configDirForPlatform", () => {
  it("prefers the arch-specific mac config when present", () => {
    const d = tmp();
    mkdirSync(join(d, "config_mac_arm"), { recursive: true });
    mkdirSync(join(d, "config_mac"), { recursive: true });
    expect(configDirForPlatform(d, "darwin-arm64")).toBe(join(d, "config_mac_arm"));
  });

  it("falls back to the arch-generic mac config on older jdtls builds", () => {
    const d = tmp();
    mkdirSync(join(d, "config_mac"), { recursive: true }); // no config_mac_arm
    expect(configDirForPlatform(d, "darwin-arm64")).toBe(join(d, "config_mac"));
  });

  it("darwin-x64 always uses config_mac (no arch split observed)", () => {
    const d = tmp();
    mkdirSync(join(d, "config_mac"), { recursive: true });
    expect(configDirForPlatform(d, "darwin-x64")).toBe(join(d, "config_mac"));
  });

  it("linux-x64 uses config_linux, win32-x64 uses config_win", () => {
    const d = tmp();
    mkdirSync(join(d, "config_linux"), { recursive: true });
    mkdirSync(join(d, "config_win"), { recursive: true });
    expect(configDirForPlatform(d, "linux-x64")).toBe(join(d, "config_linux"));
    expect(configDirForPlatform(d, "win32-x64")).toBe(join(d, "config_win"));
  });

  it("returns null when no matching config dir exists", () => {
    const d = tmp();
    expect(configDirForPlatform(d, "linux-x64")).toBeNull();
  });
});

describe("findEquinoxLauncher", () => {
  it("finds the main launcher jar, ignoring platform-specific fragment jars", () => {
    const d = tmp();
    const plugins = join(d, "plugins");
    mkdirSync(plugins, { recursive: true });
    writeFileSync(join(plugins, "org.eclipse.equinox.launcher_1.7.200.v20260619-2039.jar"), "");
    writeFileSync(join(plugins, "org.eclipse.equinox.launcher.cocoa.macosx.aarch64_1.2.1600.jar"), "");
    writeFileSync(join(plugins, "org.eclipse.equinox.launcher.gtk.linux.x86_64_1.2.1600.jar"), "");
    writeFileSync(join(plugins, "org.eclipse.equinox.launcher.win32.win32.x86_64_1.3.100.jar"), "");

    expect(findEquinoxLauncher(d)).toBe(
      join(plugins, "org.eclipse.equinox.launcher_1.7.200.v20260619-2039.jar"));
  });

  it("returns null when plugins dir or launcher jar is missing", () => {
    expect(findEquinoxLauncher(tmp())).toBeNull();
    const d = tmp();
    mkdirSync(join(d, "plugins"), { recursive: true });
    expect(findEquinoxLauncher(d)).toBeNull();
  });
});

describe("findJavaExecutable", () => {
  it("finds bin/java nested under a version-specific top-level directory", () => {
    const d = tmp();
    const nested = join(d, "jdk-17.0.19+10-jre", "bin");
    mkdirSync(nested, { recursive: true });
    writeFileSync(join(nested, "java"), "");
    expect(findJavaExecutable(d, "linux-x64")).toBe(join(nested, "java"));
  });

  it("looks for java.exe on win32-x64", () => {
    const d = tmp();
    const nested = join(d, "jdk-17.0.19+10-jre", "bin");
    mkdirSync(nested, { recursive: true });
    writeFileSync(join(nested, "java.exe"), "");
    expect(findJavaExecutable(d, "win32-x64")).toBe(join(nested, "java.exe"));
  });

  it("returns null when not found within the depth budget", () => {
    const d = tmp();
    mkdirSync(join(d, "a", "b", "c", "d", "bin"), { recursive: true });
    writeFileSync(join(d, "a", "b", "c", "d", "bin", "java"), "");
    expect(findJavaExecutable(d, "linux-x64", 2)).toBeNull();
  });
});

describe("jdtlsDataDir", () => {
  it("is deterministic and distinguishes different workspace paths", () => {
    const a = jdtlsDataDir("/rt", "/Users/x/project-a");
    const b = jdtlsDataDir("/rt", "/Users/x/project-b");
    expect(a).not.toBe(b);
    expect(jdtlsDataDir("/rt", "/Users/x/project-a")).toBe(a);
  });

  it("does not collide on same-basename different-parent workspaces (unlike upstream's basename-only hash)", () => {
    const a = jdtlsDataDir("/rt", "/Users/x/project");
    const b = jdtlsDataDir("/rt", "/Users/y/project");
    expect(a).not.toBe(b);
  });
});

describe("buildJdtlsCommand", () => {
  it("quotes paths containing spaces so the Rust tokenizer splits correctly", () => {
    const cmd = buildJdtlsCommand({
      javaExecutable: "/AI editor/runtime/jre/bin/java",
      launcherJar: "/AI editor/runtime/jdtls/plugins/org.eclipse.equinox.launcher_1.7.200.jar",
      configDir: "/AI editor/runtime/jdtls/config_mac_arm",
      dataDir: "/AI editor/runtime/jdtls-data/abc123",
    });
    expect(cmd).toContain('"/AI editor/runtime/jre/bin/java"');
    expect(cmd).toContain('-Dosgi.sharedConfiguration.area="/AI editor/runtime/jdtls/config_mac_arm"');
    expect(cmd).toContain('-jar "/AI editor/runtime/jdtls/plugins/org.eclipse.equinox.launcher_1.7.200.jar"');
    expect(cmd).toContain('-data "/AI editor/runtime/jdtls-data/abc123"');
  });

  it("leaves unspaced paths unquoted", () => {
    const cmd = buildJdtlsCommand({
      javaExecutable: "/rt/jre/bin/java",
      launcherJar: "/rt/jdtls/plugins/launcher.jar",
      configDir: "/rt/jdtls/config_linux",
      dataDir: "/rt/jdtls-data/abc",
    });
    expect(cmd.startsWith("/rt/jre/bin/java ")).toBe(true);
  });

  it("includes the required JVM flags verbatim", () => {
    const cmd = buildJdtlsCommand({
      javaExecutable: "java",
      launcherJar: "launcher.jar",
      configDir: "config_linux",
      dataDir: "data",
    });
    expect(cmd).toContain("-Declipse.application=org.eclipse.jdt.ls.core.id1");
    expect(cmd).toContain("--add-opens java.base/java.util=ALL-UNNAMED");
    expect(cmd).toContain("--add-opens java.base/java.lang=ALL-UNNAMED");
  });
});
