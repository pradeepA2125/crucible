import { chmodSync, mkdirSync, mkdtempSync, symlinkSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { readBackendToken, tokenPath } from "../src/runtime/backend-token.js";

const TOKEN = "a".repeat(43);

function home(): string {
  const h = mkdtempSync(join(tmpdir(), "home-"));
  mkdirSync(join(h, ".crucible", "run"), { recursive: true, mode: 0o700 });
  return h;
}

function put(h: string, port: number, content: string, mode = 0o600): void {
  writeFileSync(tokenPath(port, h), content);
  chmodSync(tokenPath(port, h), mode);
}

describe.skipIf(process.platform === "win32")("readBackendToken", () => {
  it("reads a well-formed owner-only file", () => {
    const h = home();
    put(h, 1, TOKEN);
    expect(readBackendToken(1, h)).toBe(TOKEN);
  });
  it("is undefined for a missing file, and does not cache the miss", () => {
    const h = home();
    expect(readBackendToken(2, h)).toBeUndefined();
    put(h, 2, TOKEN);
    expect(readBackendToken(2, h)).toBe(TOKEN);
  });
  it("refuses group-readable, symlinked and malformed files", () => {
    const h = home();
    put(h, 3, TOKEN, 0o640);
    expect(readBackendToken(3, h)).toBeUndefined();
    put(h, 4, TOKEN);
    symlinkSync(tokenPath(4, h), tokenPath(5, h));
    expect(readBackendToken(5, h)).toBeUndefined();
    put(h, 6, `${TOKEN}\n`);
    expect(readBackendToken(6, h)).toBeUndefined();
  });
  it("re-reads after a restart rewrites the file", () => {
    const h = home();
    put(h, 7, TOKEN);
    expect(readBackendToken(7, h)).toBe(TOKEN);
    put(h, 7, "b".repeat(43));
    const later = new Date(Date.now() + 5000);
    utimesSync(tokenPath(7, h), later, later);
    expect(readBackendToken(7, h)).toBe("b".repeat(43));
  });
});
