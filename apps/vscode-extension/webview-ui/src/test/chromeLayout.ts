import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// Use installed Chrome without adding a browser download to the test suite.
export const chrome = process.env.CHROME_BIN ?? [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
].find(existsSync);

export function dumpChromeDom(file: string, extraArgs: string[] = []): string {
  if (!chrome) throw new Error("Chrome is required for layout measurements");
  // Vitest runs layout files in parallel. Never share Chrome's default profile
  // (or its singleton lock) with another worker or the user's browser.
  const profile = mkdtempSync(join(tmpdir(), "layout-chrome-"));
  try {
    return execFileSync(chrome, [
      "--headless", "--no-sandbox", "--disable-gpu", "--no-first-run",
      "--disable-background-networking", "--disable-dev-shm-usage",
      `--user-data-dir=${profile}`, ...extraArgs, "--dump-dom", pathToFileURL(file).href,
    ], {
      encoding: "utf8", timeout: 20000, killSignal: "SIGKILL",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } finally {
    rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  }
}
