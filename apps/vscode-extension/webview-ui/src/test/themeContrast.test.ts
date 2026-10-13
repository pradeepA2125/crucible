import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(join(__dirname, "..", "styles", "themes.css"), "utf8");

/** Declarations of the first rule whose selector list contains `selector` exactly. */
function block(selector: string): Record<string, string> {
  const rule = /([^{}]+)\{([^{}]*)\}/g;
  for (const m of CSS.matchAll(rule)) {
    const selectors = m[1]!.replace(/\/\*[\s\S]*?\*\//g, "").split(",").map((s) => s.trim());
    if (!selectors.includes(selector)) continue;
    const out: Record<string, string> = {};
    for (const d of m[2]!.split(";")) {
      const i = d.indexOf(":");
      if (i > 0) out[d.slice(0, i).trim()] = d.slice(i + 1).trim();
    }
    return out;
  }
  throw new Error(`no rule for selector ${selector}`);
}

function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}
function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

const SURFACES = ["--surface-0", "--surface-1", "--surface-2", "--surface-3", "--input-bg"];
const TEXTS = [
  "--text-1", "--text-2", "--text-3", "--on-surface-0", "--on-surface-1", "--on-surface-2",
  "--on-surface-3", "--input-fg", "--input-placeholder", "--success", "--danger", "--warning",
];
const PALETTES = [
  { tone: "dark", selector: ':root[data-theme="dark"]' },
  { tone: "light", selector: ':root[data-theme="light"]' },
] as const;
const ACCENTS = ["blue", "violet", "ember"] as const;

describe("theme palettes meet WCAG AA (4.5:1)", () => {
  for (const { tone, selector } of PALETTES) {
    const palette = block(selector);
    for (const text of TEXTS) {
      for (const surface of SURFACES) {
        it(`${tone}: ${text} on ${surface}`, () => {
          expect(ratio(palette[text]!, palette[surface]!)).toBeGreaterThanOrEqual(4.5);
        });
      }
    }
    for (const accent of ACCENTS) {
      const a = block(`[data-tone="${tone}"][data-accent="${accent}"]`);
      it(`${tone}/${accent}: on-accent on accent-fill`, () => {
        expect(ratio(a["--on-accent"]!, a["--accent-fill"]!)).toBeGreaterThanOrEqual(4.5);
      });
      for (const surface of SURFACES) {
        it(`${tone}/${accent}: accent link on ${surface}`, () => {
          expect(ratio(a["--accent"]!, palette[surface]!)).toBeGreaterThanOrEqual(4.5);
        });
      }
    }
  }
});

describe("vscode and hc mode token chains", () => {
  it("every --vscode-* reference carries a fallback", () => {
    // var(--vscode-x) with no comma before its closing paren has no fallback.
    expect(CSS.match(/var\(--vscode-[\w-]+\)/g) ?? []).toEqual([]);
  });
});
