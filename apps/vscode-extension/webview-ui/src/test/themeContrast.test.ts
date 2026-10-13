import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(join(__dirname, "..", "styles", "themes.css"), "utf8");

/** Declarations for `selector`, merging every rule whose selector list contains it
 * exactly, in source order (later declarations win, as in the cascade). */
export function blockIn(css: string, selector: string): Record<string, string> {
  const out: Record<string, string> = {};
  let found = false;
  for (const m of css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!m[1]!.split(",").map((x) => x.trim()).includes(selector)) continue;
    found = true;
    for (const d of m[2]!.split(";")) {
      const i = d.indexOf(":");
      if (i > 0) out[d.slice(0, i).trim()] = d.slice(i + 1).trim();
    }
  }
  if (!found) throw new Error(`no rule for selector ${selector}`);
  return out;
}
const block = (selector: string) => blockIn(CSS, selector);

function luminance(hex: string): number {
  if (!/^#[0-9a-fA-F]{6}$/.test(hex)) throw new Error(`not a #rrggbb colour: ${hex}`);
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}
function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

/** Every var(...) in `value` with its fallback (null when absent), parsed by parentheses. */
function varCalls(value: string): { name: string; fallback: string | null }[] {
  const calls: { name: string; fallback: string | null }[] = [];
  for (let i = value.indexOf("var("); i !== -1; i = value.indexOf("var(", i + 4)) {
    let depth = 0, comma = -1, end = -1;
    for (let j = i + 3; j < value.length; j++) {
      if (value[j] === "(") depth++;
      else if (value[j] === ")") { depth--; if (depth === 0) { end = j; break; } }
      else if (value[j] === "," && depth === 1 && comma === -1) comma = j;
    }
    const inner = value.slice(i + 4, end);
    calls.push({
      name: (comma === -1 ? inner : value.slice(i + 4, comma)).trim(),
      fallback: comma === -1 ? null : value.slice(comma + 1, end).trim(),
    });
  }
  return calls;
}
/** The literal a var() chain ends in: var(--a, var(--b, #123456)) → #123456. */
function terminal(value: string): string {
  let v = value.trim();
  while (v.startsWith("var(")) {
    const first = varCalls(v)[0]!;
    if (first.fallback === null) return v;
    v = first.fallback;
  }
  return v;
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
      it(`${tone}/${accent}: on-accent on the hover fill`, () => {
        expect(ratio(a["--on-accent"]!, a["--accent-fill-hover"]!)).toBeGreaterThanOrEqual(4.5);
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
  const vscode = block(':root[data-theme="vscode"]');
  const dark = block(':root[data-theme="dark"]');

  it("every --vscode-* reference carries a fallback (parsed, whitespace-tolerant)", () => {
    const missing = [...CSS.matchAll(/--[\w-]+\s*:\s*([^;]+);/g)]
      .flatMap((m) => varCalls(m[1]!))
      .filter((c) => c.name.startsWith("--vscode-") && c.fallback === null)
      .map((c) => c.name);
    expect(missing).toEqual([]);
  });

  it("vscode chains end in the Crucible Dark value wherever Dark uses a literal colour", () => {
    const mismatched = Object.entries(vscode)
      .filter(([token, value]) => value.includes("var(--vscode-") && /^#[0-9a-fA-F]{6}$/.test(dark[token] ?? ""))
      .filter(([token, value]) => terminal(value).toLowerCase() !== dark[token]!.toLowerCase())
      .map(([token, value]) => `${token}: ${terminal(value)} ≠ ${dark[token]}`);
    expect(mismatched).toEqual([]);
  });

  it("vscode surfaces are tinted from the page so general text stays readable on them", () => {
    for (const n of [1, 2, 3]) {
      expect(vscode[`--surface-${n}`]).toMatch(/^color-mix\(in srgb, var\(--surface-0\) \d+%, var\(--text-1\)\)$/);
      expect(vscode[`--on-surface-${n}`]).toBe("var(--text-1)");
    }
  });

  it("high contrast replaces every translucent legacy tint and border", () => {
    const hc = block(':root[data-theme="hc"][data-accent]');
    for (const t of ["--accent-bg", "--accent-bg-2", "--accent-glow", "--success-bg", "--danger-bg", "--warning-bg"]) {
      expect(hc[t], t).toBe("transparent");
    }
    for (const t of ["--green-brd", "--red-brd", "--hairline", "--accent-border", "--warning-border"]) {
      expect(hc[t], t).toMatch(/^var\(--vscode-contrast/);
    }
  });
});

describe("the palette parser itself", () => {
  it("lets a later rule for the same selector win", () => {
    const css = ':root[data-theme="light"] { --text-3: #6b6b6b; }\n:root[data-theme="light"] { --text-3: #ffffff; }';
    expect(blockIn(css, ':root[data-theme="light"]')["--text-3"]).toBe("#ffffff");
  });
  it("rejects a malformed colour instead of reading part of it", () => {
    expect(() => ratio("#6b6b6b-invalid", "#ffffff")).toThrow(/not a #rrggbb/);
  });
  it("reads fallbacks through nested var() calls", () => {
    expect(terminal("var( --vscode-a , var(--vscode-b, #123456))")).toBe("#123456");
  });
});

describe("native controls follow the theme (web-design-guidelines: color-scheme)", () => {
  it("sets color-scheme per tone so native radios, checkboxes and scrollbars match", () => {
    expect(block(':root[data-tone="dark"]')["color-scheme"]).toBe("dark");
    expect(block(':root[data-tone="light"]')["color-scheme"]).toBe("light");
  });
  it("tints native controls with the accent", () => {
    expect(block(':root[data-tone="dark"]')["accent-color"]).toBe("var(--accent-fill)");
    expect(block(':root[data-tone="light"]')["accent-color"]).toBe("var(--accent-fill)");
  });
});
