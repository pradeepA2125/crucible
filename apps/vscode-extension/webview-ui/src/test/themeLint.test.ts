import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");

/** Files that must use theme tokens only. Each phase appends what it migrates
 * (spec Testing → Lint). styles/themes.css is the token definition file and is exempt. */
export const LINT_SCOPE = [
  "index.css",
  "theme.ts",
  "setup/StepRail.tsx",
  "setup/SetupApp.tsx",
  "memory/MemoryApp.tsx",
  "components/SettingsDrawer.tsx",
];
// Linted for literal colours only in Phase 1; their sizes and radii migrate with their layouts later.
const COLOR_ONLY = new Set(["setup/StepRail.tsx", "setup/SetupApp.tsx", "memory/MemoryApp.tsx", "components/SettingsDrawer.tsx"]);

const COLOR_LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\(/;
const TW_SIZE = /\btext-\[\d|\btext-(xs|sm|base|lg|xl|2xl)\b/;
const TW_RADIUS = /\brounded(?![\w-])|\brounded-\[\d|\brounded-(sm|md|lg|xl|2xl|3xl)\b/;
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
        .filter(({ text }) => COLOR_ONLY.has(file)
          ? COLOR_LITERAL.test(text)
          : COLOR_LITERAL.test(text) || TW_SIZE.test(text) || TW_RADIUS.test(text)
            || INLINE_SIZE.test(text) || (file.endsWith(".css") && CSS_SIZE.test(text)))
        .map(({ line, text }) => `${file}:${line}: ${text.trim()}`);
      expect(offenders).toEqual([]);
    });
  }
});

/** Transcript components must be flat (spec §2: gradients, glows, coloured shadows and
 * shimmer borders removed). A line ending in `// decoration-ok: <reason>` is allowed. */
export const DECORATION_SCOPE = [
  "components/shared/buttons.tsx", "components/shared/Switch.tsx", "components/shared/ThinkingBlock.tsx",
  "components/shared/ToolDetailPanel.tsx", "components/shared/CardShell.tsx", "components/shared/MarkdownContent.tsx",
  "components/messages/TodoCard.tsx", "components/messages/PlanCard.tsx", "components/messages/DiffCard.tsx",
  "components/messages/ErrorCard.tsx", "components/messages/UserMessage.tsx", "components/messages/SessionStrip.tsx",
  "components/messages/gates/ClarifyGate.tsx", "components/messages/gates/CommandGate.tsx",
  "components/messages/gates/EditGate.tsx", "components/messages/gates/McpGate.tsx",
  "components/messages/gates/ModeGate.tsx", "components/messages/gates/ScopeGate.tsx",
  "components/messages/gates/StepGate.tsx", "components/messages/gates/TeamPlanGate.tsx",
  "components/messages/gates/ValidationGate.tsx",
];
const DECORATION = /linear-gradient|radial-gradient|--accent-glow|shimmer-bg|0_10px_24px|boxShadow\s*:/;

describe("transcript decoration lint", () => {
  for (const file of DECORATION_SCOPE) {
    it(`${file} has no gradients or glows`, () => {
      const offenders = lines(file)
        .map((text, i) => ({ text, line: i + 1 }))
        .filter(({ text }) => DECORATION.test(text) && !text.includes("decoration-ok:"))
        .map(({ line, text }) => `${file}:${line}: ${text.trim()}`);
      expect(offenders).toEqual([]);
    });
  }
});

/** Phase 2 (spec Testing → Lint): every transcript component, brand assets excepted. */
function tsxUnder(dir: string): string[] {
  return readdirSync(join(SRC, dir)).flatMap((name) => {
    const rel = `${dir}/${name}`;
    if (statSync(join(SRC, rel)).isDirectory()) return tsxUnder(rel);
    return name.endsWith(".tsx") && !name.includes(".test.") ? [rel] : [];
  });
}
const BRAND_EXEMPT = new Set(["components/shared/ChatGPTBrand.tsx", "components/shared/ClaudeBrand.tsx"]);
export const PHASE2_SCOPE = [
  ...tsxUnder("components/messages"), ...tsxUnder("components/activity"), ...tsxUnder("components/shared"),
  "components/ThreadView.tsx", "components/MessageRow.tsx",
].filter((f) => !BRAND_EXEMPT.has(f));

describe("theme lint — Phase 2 transcript scope", () => {
  for (const file of PHASE2_SCOPE) {
    it(`${file} uses tokens only`, () => {
      const offenders = lines(file)
        .map((text, i) => ({ text, line: i + 1 }))
        .filter(({ text }) => !text.trim().startsWith("//") && !text.trim().startsWith("*"))
        .filter(({ text }) => COLOR_LITERAL.test(text) || TW_SIZE.test(text) || TW_RADIUS.test(text) || INLINE_SIZE.test(text))
        .map(({ line, text }) => `${file}:${line}: ${text.trim()}`);
      expect(offenders).toEqual([]);
    });
  }
});
