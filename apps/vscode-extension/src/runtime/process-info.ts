// vscode-free. Who owns a pid, what it runs, and when it started (spec §3.7) — the
// facts that let the extension kill a stale backend and nothing else.
import type { ExecOutcome, ProcessInfo } from "./backend-process.js";

export function parseEtime(etime: string): number | null {
  const match = /^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$/.exec(etime.trim());
  if (!match) return null;
  const [, days, hours, minutes, seconds] = match;
  return Number(days ?? 0) * 86400 + Number(hours ?? 0) * 3600
    + Number(minutes) * 60 + Number(seconds);
}

export function parsePsLine(line: string, nowSec: number): ProcessInfo | null {
  const [, uid, etime, command] = /^\s*(\d+)\s+(\S+)\s+(.+?)\s*$/.exec(line) ?? [];
  if (uid === undefined || etime === undefined || command === undefined) return null;
  const elapsed = parseEtime(etime);
  if (elapsed === null) return null;
  return { uid: Number(uid), startedAtSec: nowSec - elapsed, command };
}

export async function readProcessInfo(
  pid: number,
  exec: (cmd: string, args: string[], timeoutMs: number) => Promise<ExecOutcome>,
): Promise<ProcessInfo | null> {
  if (process.platform === "win32") return null;
  const out = await exec("/usr/bin/env",
    ["LC_ALL=C", "ps", "-ww", "-o", "uid=,etime=,command=", "-p", String(pid)], 5000);
  if (out.code !== 0) return null;
  const line = out.stdout.split("\n").find((l) => l.trim() !== "");
  return line ? parsePsLine(line, Math.floor(Date.now() / 1000)) : null;
}
