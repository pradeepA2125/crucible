import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const INDEX = readFileSync(join(__dirname, "..", "index.css"), "utf8");
const THEMES = readFileSync(join(__dirname, "..", "styles", "themes.css"), "utf8");

describe("Phase 2 scale utilities (spec §1 type and radius scales)", () => {
  it.each([
    ["--text-caption", "var(--fs-caption)"],
    ["--text-meta", "var(--fs-meta)"],
    ["--text-body", "var(--fs-body)"],
    ["--text-prose", "var(--fs-prose)"],
    ["--radius-control", "var(--r-control)"],
    ["--radius-card", "var(--r-card)"],
    ["--radius-composer", "var(--r-composer)"],
  ])("@theme maps %s to %s", (name, value) => {
    const theme = INDEX.slice(INDEX.indexOf("@theme {"), INDEX.indexOf("}", INDEX.indexOf("@theme {")));
    expect(theme).toContain(`${name}: ${value};`);
  });

  it("defines diff row tints and switches them off in high contrast", () => {
    expect(THEMES).toMatch(/--diff-add-bg: color-mix\(in srgb, var\(--diff-add\) \d+%, transparent\);/);
    expect(THEMES).toMatch(/--diff-del-bg: color-mix\(in srgb, var\(--diff-del\) \d+%, transparent\);/);
    const hc = THEMES.slice(THEMES.indexOf(':root[data-theme="hc"][data-accent] {'));
    expect(hc).toContain("--diff-add-bg: transparent;");
    expect(hc).toContain("--diff-del-bg: transparent;");
  });

  it("animates the live verb only when motion is allowed", () => {
    expect(INDEX).toMatch(/\.shimmer-text \{[^}]*animation: shimmer/);
    expect(INDEX).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\.shimmer-text \{ animation: none;/);
  });
});
