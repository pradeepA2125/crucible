import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { VoiceDiagnostics } from "../src/voice/diagnostics.js";

describe("voice diagnostics", () => {
  it("appends one JSON line per event with time, source and data", async () => {
    const dir = await mkdtemp(join(tmpdir(), "voice-diag-"));
    const path = join(dir, "voice", "diagnostics.jsonl");
    const log = new VoiceDiagnostics(path, 1_000_000, () => new Date("2026-11-10T12:00:00.000Z"));
    log.write("app", "pause", { silenceMs: 560 });
    log.write("host", "decide", { text: "use sqlite", action: "reply" });
    await log.flush();
    const lines = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    expect(lines).toEqual([
      { at: "2026-11-10T12:00:00.000Z", source: "app", event: "pause", silenceMs: 560 },
      { at: "2026-11-10T12:00:00.000Z", source: "host", event: "decide", text: "use sqlite", action: "reply" },
    ]);
  });

  it("keeps its folder private to the user", async () => {
    const dir = await mkdtemp(join(tmpdir(), "voice-diag-"));
    const log = new VoiceDiagnostics(join(dir, "voice", "diagnostics.jsonl"));
    log.write("app", "pause", {});
    await log.flush();
    expect((await stat(join(dir, "voice"))).mode & 0o777).toBe(0o700);
  });

  it("rotates the file once it passes the size cap", async () => {
    const dir = await mkdtemp(join(tmpdir(), "voice-diag-"));
    const path = join(dir, "diagnostics.jsonl");
    await writeFile(path, "x".repeat(200));
    const log = new VoiceDiagnostics(path, 100);
    log.write("app", "levels", { peak: 0.01 });
    await log.flush();
    expect((await stat(`${path}.1`)).size).toBe(200);
    expect((await readFile(path, "utf8")).split("\n").filter(Boolean)).toHaveLength(1);
  });

  it("never throws when the file cannot be written", async () => {
    const log = new VoiceDiagnostics("/dev/null/not-a-dir/diagnostics.jsonl");
    log.write("app", "pause", {});
    await expect(log.flush()).resolves.toBeUndefined();
  });
});
