import { describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { migrateLegacyRuntimeRoot } from "../src/runtime/legacy-migration";

function fakeHome(): string {
  return mkdtempSync(join(tmpdir(), "crucible-home-"));
}

describe("migrateLegacyRuntimeRoot", () => {
  it("renames ~/.ai-editor to ~/.crucible when a runtime exists", () => {
    const home = fakeHome();
    mkdirSync(join(home, ".ai-editor", "runtime"), { recursive: true });
    writeFileSync(join(home, ".ai-editor", "runtime", "runtime.json"), "{}");

    const dir = migrateLegacyRuntimeRoot(home);

    expect(dir).toBe(join(home, ".crucible", "runtime"));
    expect(readFileSync(join(dir, "runtime.json"), "utf8")).toBe("{}");
    expect(existsSync(join(home, ".ai-editor"))).toBe(false);
  });

  it("returns the new path untouched on a fresh machine", () => {
    const home = fakeHome();
    expect(migrateLegacyRuntimeRoot(home)).toBe(join(home, ".crucible", "runtime"));
    expect(existsSync(join(home, ".crucible"))).toBe(false); // installer creates it later
  });

  it("never clobbers an existing ~/.crucible", () => {
    const home = fakeHome();
    mkdirSync(join(home, ".crucible", "runtime"), { recursive: true });
    writeFileSync(join(home, ".crucible", "runtime", "runtime.json"), "new");
    mkdirSync(join(home, ".ai-editor", "runtime"), { recursive: true });

    const dir = migrateLegacyRuntimeRoot(home);

    expect(readFileSync(join(dir, "runtime.json"), "utf8")).toBe("new");
    expect(existsSync(join(home, ".ai-editor"))).toBe(true); // legacy left alone
  });
});
