import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { helperEnvironment, helperPath, helperUsable, LineWorker } from "../src/voice/workers.js";

const directories: string[] = [];
afterEach(async () => { await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true }))); });
async function scratch(): Promise<string> {
  const path = await mkdtemp(join(tmpdir(), "crucible-voice-workers-"));
  directories.push(path);
  return path;
}

describe("voice helpers on every platform", () => {
  it("win32 helpers carry .exe and get a Windows environment", () => {
    expect(helperPath("C:\\h", "crucible-whisper", "win32")).toMatch(/crucible-whisper\.exe$/);
    expect(helperEnvironment("win32", { SystemRoot: "C:\\Windows", TEMP: "C:\\T", PATH: "x" }, "C:\\h")).toEqual({
      SystemRoot: "C:\\Windows", windir: "C:\\Windows", TEMP: "C:\\T", TMP: "C:\\T", PATH: "C:\\h;C:\\Windows\\System32",
    });
  });

  it("posix helpers keep today's environment", () => {
    expect(helperPath("/h", "crucible-whisper", "linux")).toBe(join("/h", "crucible-whisper"));
    const env = helperEnvironment("linux", { TMPDIR: "/tmp/x", PATH: "/opt/bin" }, "/h");
    expect(env).toEqual({ PATH: "/usr/bin:/bin", LANG: "en_US.UTF-8", TMPDIR: "/tmp/x" });
    expect(env).not.toHaveProperty("SystemRoot");
  });

  it("helper paths and environment survive spaces and accents", () => {
    const helpers = "C:\\Users\\José Smith\\AppData\\Roaming\\Crucible\\voice-native";
    expect(helperPath(helpers, "crucible-whisper", "win32").startsWith(helpers)).toBe(true);
    expect(helperEnvironment("win32", { SystemRoot: "C:\\Windows" }, helpers).PATH?.startsWith(`${helpers};`)).toBe(true);
  });

  it("a Windows helper needs no execute bit", async () => {
    const dir = await scratch();
    const file = join(dir, "crucible-whisper.exe");
    await writeFile(file, "", { mode: 0o600 });
    expect(await helperUsable(file, "win32")).toBe(true);
    expect(await helperUsable(join(dir, "missing"), "win32")).toBe(false);
  });

  // Windows has no execute bit to withhold, so this half only holds on POSIX file systems.
  it.skipIf(process.platform === "win32")("a POSIX helper needs its execute bit", async () => {
    const dir = await scratch();
    const file = join(dir, "crucible-whisper");
    await writeFile(file, "", { mode: 0o600 });
    expect(await helperUsable(file, "linux")).toBe(false);
  });

  it.skipIf(process.platform === "win32")("windows line endings parse as lines", async () => {
    const dir = await scratch();
    const script = join(dir, "helper");
    await writeFile(script, "#!/bin/sh\nprintf 'ready\\r\\n'\ncat >/dev/null\n");
    await chmod(script, 0o700);
    const lines: string[] = [];
    const worker = new LineWorker(script, [], line => lines.push(line), () => {});
    await vi.waitFor(() => expect(lines).toEqual(["ready"]));
    await worker.close();
  });
});
