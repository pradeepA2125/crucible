import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");

/** Files that must use theme tokens only. Each phase appends what it migrates
 * (spec Testing → Lint). styles/themes.css is the token definition file and is exempt. */
export const LINT_SCOPE = ["index.css"];

const COLOR_LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\(/;
const TW_SIZE = /\btext-\[\d|\btext-(xs|sm|base|lg|xl|2xl)\b/;
const TW_RADIUS = /\brounded-\[\d|\brounded-(sm|md|lg|xl|2xl|3xl)\b/;
const INLINE_SIZE = /\b(fontSize|borderRadius)\s*:\s*(\d|["'`]\d)/;
const CSS_SIZE = /\b(font-size|border-radius)\s*:\s*(?!\s|var\()[^;]*\d/;

function lines(file: string): string[] {
  return readFileSync(join(SRC, file), "utf8").split("\n");
}

describe("theme lint (in-scope files use tokens only)", () => {
  for (const file of LINT_SCOPE) {
    it(`${file} has no literal colours, sizes or radii`, () => {
      const offenders = lines(file)
        .map((text, i) => ({ text, line: i + 1 }))
        .filter(({ text }) => !text.trim().startsWith("/*") && !text.trim().startsWith("*") && !text.trim().startsWith("//"))
        .filter(({ text }) =>
          COLOR_LITERAL.test(text) || TW_SIZE.test(text) || TW_RADIUS.test(text)
          || INLINE_SIZE.test(text) || (file.endsWith(".css") && CSS_SIZE.test(text)))
        .map(({ line, text }) => `${file}:${line}: ${text.trim()}`);
      expect(offenders).toEqual([]);
    });
  }
});
