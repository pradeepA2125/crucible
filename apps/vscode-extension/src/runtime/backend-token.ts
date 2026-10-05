// vscode-free. Reads <home>/.crucible/run/agentd-<port>.token (spec §3.2): O_NOFOLLOW,
// then fstat THE DESCRIPTOR — never stat-then-open.
import { closeSync, constants, fstatSync, openSync, readSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
const cache = new Map<string, { mtimeMs: number; token: string }>();

export function runDir(home: string = homedir()): string {
  return join(home, ".crucible", "run");
}

export function tokenPath(port: number, home: string = homedir()): string {
  return join(runDir(home), `agentd-${port}.token`);
}

export function readBackendToken(port: number, home: string = homedir()): string | undefined {
  const path = tokenPath(port, home);
  let fd: number;
  try {
    fd = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  } catch {
    cache.delete(path);
    return undefined; // missing, or a symlink (ELOOP)
  }
  try {
    const st = fstatSync(fd);
    if (process.platform !== "win32") {
      const uid = process.getuid?.();
      if (!st.isFile() || (uid !== undefined && st.uid !== uid) || (st.mode & 0o077) !== 0) {
        cache.delete(path);
        return undefined;
      }
    }
    const hit = cache.get(path);
    if (hit && hit.mtimeMs === st.mtimeMs) return hit.token;
    const buf = Buffer.alloc(44);
    const n = readSync(fd, buf, 0, 44, 0);
    const text = buf.subarray(0, n).toString("ascii");
    if (n !== 43 || !TOKEN_RE.test(text)) {
      cache.delete(path);
      return undefined;
    }
    cache.set(path, { mtimeMs: st.mtimeMs, token: text });
    return text;
  } finally {
    closeSync(fd);
  }
}
