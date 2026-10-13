# Theming Phase 1: Theme Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Crucible webview (chat, settings, setup, memory) follows a configurable appearance (Match VS Code / Crucible Dark / Crucible Light, plus automatic high contrast) and accent (Blue / Violet / Ember). Users change both from a new Settings › Appearance section, which works even when the backend is down. Layouts stay as they are.

**Architecture:**
- **Token file.** A new `webview-ui/src/styles/themes.css` defines semantic tokens per theme, keyed on attributes on `<html>`.
- **Aliases.** The existing Tailwind `@theme` names in `index.css` become aliases of those tokens, so the 858 existing `var(--color-…)` references follow the theme without edits.
- **Resolver.** A small webview module, `theme.ts`, resolves the effective theme from the configured values plus VS Code's body classes, and re-resolves on live changes.
- **Host.** Two VS Code settings feed a vscode-free `ThemeHub`. The hub stamps attributes into each panel's HTML and broadcasts `themeChanged`.

**Tech Stack:** Tailwind v4 (`@theme`), React 18, Vite 6, vitest (jsdom for the webview, node for the host), the VS Code extension API (through `src/vscode-shim.d.ts`).

**Spec:** `docs/superpowers/specs/2026-10-13-codex-style-chat-theming-design.md` (revision 5). This plan is spec §1 (Theme foundation) plus the Phase 1 rows of Testing. Phases 2 (transcript) and 3 (composer and approval modes) get their own plans, written against the code this phase ships.

## Global Constraints

- **Appearance setting:** `crucible.appearance` is `"vscode" | "dark" | "light"`, default `"vscode"`.
- **Accent setting:** `crucible.accent` is `"blue" | "violet" | "ember"`, default `"blue"`.
- **Scope of both settings:** application scope. They need no backend.
- **Contrast:** in the `dark` and `light` palettes, every text token reaches ≥ 4.5:1 on every surface. That covers links, and `--on-accent` on `--accent-fill`. In `vscode` and `hc` mode, contrast is up to the theme author; there we only use paired roles.
- **High contrast wins.** `vscode-high-contrast` or `vscode-high-contrast-light` on `<body>` resolves to `hc` whatever the appearance and accent settings say. Accent tints, washes, shadows and opacity effects are off in `hc`.
- **Fallbacks.** Every `var(--vscode-…)` in a token definition ends in a fallback; the final fallback is the Crucible Dark value of the same token.
- **Graph is excluded.** Don't touch `webview-ui/src/graph/**`.
- **No layout changes in this phase.** Only colour, shadow and radius values move onto tokens.
- **Type scale:** derived from `--vscode-font-size` (the workbench UI font size), never from `--vscode-editor-font-size`.
- **Commits:** the user's CLAUDE.md says never commit without confirmation. At the start of execution, ask the user once to confirm per-task commits on branch `feat/codex-style-chat-theming`. Never push.

## Review Focus

1. **A VS Code theme that doesn't define some variables** (community themes often omit `editorWidget-foreground` or `menu-background`). The panel must still render readable colours through the fallback chain. This is pinned in Task 1 by a test that every `var(--vscode-…)` has a fallback.
2. **The user switches the editor's colour theme while a panel is open**, for example dark to high-contrast light. The panel must re-theme without a reload. Pinned in Task 3 (body-class mutation test).
3. **A hand-edited `settings.json` with an unknown value** (`"crucible.accent": "pink"`). The host falls back to the default instead of writing the bad value into the HTML. Pinned in Task 4 (normalization test).
4. **Settings opened while the backend is down.** Navigation and Appearance work, and the other sections show a retryable "can't reach backend" state instead of blanking the panel. Pinned in Task 5 (offline SettingsApp test).
5. **A `themeChanged` message reaching the chat webview's own message reducer.** It must be ignored harmlessly, with no crash and no state change. Pinned in Task 3 (`useAppState` test).

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `apps/vscode-extension/webview-ui/src/styles/themes.css` | create | All theme tokens: base, dark, light, vscode and hc, plus the accent blocks |
| `apps/vscode-extension/webview-ui/src/index.css` | modify | Import `themes.css`; turn `@theme` into aliases; move the primitives onto tokens |
| `apps/vscode-extension/webview-ui/src/theme.ts` | create | `resolveTheme`, `applyTheme`, `installThemeSync` |
| `apps/vscode-extension/webview-ui/src/{main,settings/main,setup/main,memory/main}.tsx` | modify | Call `installThemeSync()` before rendering |
| `apps/vscode-extension/webview-ui/src/test/themeContrast.test.ts` | create | WCAG contrast over the parsed `themes.css`, plus the fallback-chain check |
| `apps/vscode-extension/webview-ui/src/test/themeLint.test.ts` | create | No literal colours or off-scale sizes or radii in the in-scope files |
| `apps/vscode-extension/webview-ui/src/test/theme.test.ts` | create | Resolver, attributes, live updates |
| `apps/vscode-extension/src/theme-settings.ts` | create | vscode-free: normalization, HTML attribute injection, `ThemeHub` |
| `apps/vscode-extension/src/settings.ts` | modify | `readThemeConfig()` (vscode-bound) |
| `apps/vscode-extension/src/{chat,settings,setup,memory}-panel.ts` | modify | Inject attributes; subscribe to the hub |
| `apps/vscode-extension/src/extension.ts` | modify | Seed the hub; listen for configuration changes |
| `apps/vscode-extension/package.json` | modify | Contribute the two settings |
| `apps/vscode-extension/test/theme-settings.test.ts` | create | Host unit tests plus a drift guard against the webview's lists |
| `apps/vscode-extension/src/settings-sections.ts`, `test/settings-sections.test.ts` | modify | Add the `appearance` section |
| `apps/vscode-extension/src/settings-data.ts`, `src/settings-deps.ts`, `test/settings-data.test.ts` | modify | `settings/localState` and `settings/setAppearance` |
| `apps/vscode-extension/webview-ui/src/settings/{types.ts,sections/meta.ts,SettingsApp.tsx}` | modify | Message mirrors, the section entry, offline shell |
| `apps/vscode-extension/webview-ui/src/settings/sections/AppearanceSection.tsx` (+ test) | create | Two radio groups with swatches |
| `apps/vscode-extension/webview-ui/src/{setup/StepRail.tsx,setup/SetupApp.tsx,memory/MemoryApp.tsx,components/SettingsDrawer.tsx,components/shared/buttons.tsx,components/shared/Switch.tsx}` | modify | Colour-only literal cleanup |
| `scripts/dev/cdp-theme-check.mjs` | create | Real-browser computed-colour check over CDP |

---

### Task 1: Theme tokens, aliases and the contrast test

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/styles/themes.css`
- Modify: `apps/vscode-extension/webview-ui/src/index.css:1-54` (the `@theme` and `:root` blocks)
- Test: `apps/vscode-extension/webview-ui/src/test/themeContrast.test.ts`

**Interfaces:**
- Produces:
  - **Attributes on `<html>`:** `data-theme` (`vscode|dark|light|hc`), `data-tone` (`dark|light`), `data-accent` (`blue|violet|ember`). Task 3 sets them; Task 4 stamps their initial values.
  - **Tokens:**
    - surfaces: `--surface-0..3` with `--on-surface-0..3`;
    - text: `--text-1..3`;
    - input: `--input-bg/fg/placeholder/border`;
    - buttons: `--button-bg/fg/hover-bg/secondary-bg/secondary-fg`;
    - lines: `--border`, `--border-strong`, `--focus-ring`;
    - accent: `--accent`, `--accent-fill`, `--on-accent`, `--accent-bg`, `--accent-bg-2`, `--accent-border`, `--accent-glow`;
    - status: `--success`, `--danger`, `--warning`, `--success-bg`, `--danger-bg`, `--warning-bg`, `--warning-border`;
    - diff: `--diff-add`, `--diff-del`;
    - effects: `--shadow-popover`, `--shadow-control`, `--highlight-inset`, `--scrim`, `--scrollbar-thumb`, `--scrollbar-thumb-hover`;
    - type scale: `--fs-caption/meta/body/prose`;
    - radius scale: `--r-control/card/composer/round`.
  - **Legacy aliases kept working:** `--color-panel`, `--color-surface*`, `--color-border*`, `--color-text*`, `--color-accent*`, `--color-green/red/amber/code`, `--accent-brd`, `--green-bg/brd`, `--red-bg/brd`, `--amber-bg`, `--hairline`.

- [ ] **Step 1: Write the failing contrast test**

Create `apps/vscode-extension/webview-ui/src/test/themeContrast.test.ts`:

```ts
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/themeContrast.test.ts`
Expected: FAIL with `ENOENT … styles/themes.css`.

- [ ] **Step 3: Create `themes.css`**

Create `apps/vscode-extension/webview-ui/src/styles/themes.css`:

```css
/* Theme tokens (spec 2026-10-13 §1). Components read only these. theme.ts sets
   data-theme (vscode|dark|light|hc), data-tone (dark|light) and data-accent
   (blue|violet|ember) on <html>; the host stamps initial values so first paint
   is right. Every --vscode-* reference ends in the Crucible Dark value. */

/* Shared, palette-independent tokens. The plain :root also carries the Crucible
   Dark palette and blue accent, so a page without attributes still renders. */
:root {
  --fs-base: var(--vscode-font-size, 13px);
  --fs-caption: calc(var(--fs-base) * 11 / 13);
  --fs-meta: calc(var(--fs-base) * 12 / 13);
  --fs-body: var(--fs-base);
  --fs-prose: calc(var(--fs-base) * 14 / 13);
  --r-control: 6px;
  --r-card: 10px;
  --r-composer: 16px;
  --r-round: 999px;

  /* Derived tints. Recomputed on <html> from whichever accent and status values apply there. */
  --accent-bg: color-mix(in srgb, var(--accent-fill) 14%, transparent);
  --accent-bg-2: color-mix(in srgb, var(--accent-fill) 22%, transparent);
  --accent-border: color-mix(in srgb, var(--accent) 40%, transparent);
  --accent-glow: color-mix(in srgb, var(--accent-fill) 22%, transparent);
  --success-bg: color-mix(in srgb, var(--success) 12%, transparent);
  --danger-bg: color-mix(in srgb, var(--danger) 10%, transparent);
  --warning-bg: color-mix(in srgb, var(--warning) 12%, transparent);
  --warning-border: color-mix(in srgb, var(--warning) 30%, transparent);
}

:root,
:root[data-theme="dark"] {
  --surface-0: #181818;
  --on-surface-0: #e8e8e8;
  --surface-1: #232323;
  --on-surface-1: #e8e8e8;
  --surface-2: #202020;
  --on-surface-2: #e8e8e8;
  --surface-3: #2a2a2a;
  --on-surface-3: #e8e8e8;
  --text-1: #e8e8e8;
  --text-2: #a3a3a3;
  --text-3: #949494;
  --input-bg: #262626;
  --input-fg: #e8e8e8;
  --input-placeholder: #949494;
  --input-border: #2e2e2e;
  --button-bg: var(--accent-fill);
  --button-fg: var(--on-accent);
  --button-hover-bg: color-mix(in srgb, var(--accent-fill) 88%, white);
  --button-secondary-bg: #2a2a2a;
  --button-secondary-fg: #e8e8e8;
  --border: #2e2e2e;
  --border-strong: #3a3a3a;
  --focus-ring: var(--accent);
  --success: #3fb950;
  --danger: #ff6b61;
  --warning: #d29922;
  --diff-add: #3fb950;
  --diff-del: #ff6b61;
  --shadow-popover: 0 24px 70px -20px rgba(0, 0, 0, 0.78), 0 2px 10px rgba(0, 0, 0, 0.45);
  --shadow-control: 0 1px 2px rgba(0, 0, 0, 0.4);
  --highlight-inset: inset 0 1px 0 rgba(255, 255, 255, 0.08);
  --scrim: rgba(6, 6, 9, 0.58);
  --scrollbar-thumb: #3a3a3a;
  --scrollbar-thumb-hover: #4a4a4a;
}

:root[data-theme="light"] {
  --surface-0: #ffffff;
  --on-surface-0: #1a1a1a;
  --surface-1: #f0f0f0;
  --on-surface-1: #1a1a1a;
  --surface-2: #f6f6f6;
  --on-surface-2: #1a1a1a;
  --surface-3: #ffffff;
  --on-surface-3: #1a1a1a;
  --text-1: #1a1a1a;
  --text-2: #5c5c5c;
  --text-3: #6b6b6b;
  --input-bg: #ffffff;
  --input-fg: #1a1a1a;
  --input-placeholder: #6b6b6b;
  --input-border: #d0d0d0;
  --button-bg: var(--accent-fill);
  --button-fg: var(--on-accent);
  --button-hover-bg: color-mix(in srgb, var(--accent-fill) 88%, black);
  --button-secondary-bg: #f0f0f0;
  --button-secondary-fg: #1a1a1a;
  --border: #e3e3e3;
  --border-strong: #d0d0d0;
  --focus-ring: var(--accent);
  --success: #17742f;
  --danger: #cf222e;
  --warning: #8a5c00;
  --diff-add: #17742f;
  --diff-del: #cf222e;
  --shadow-popover: 0 24px 70px -20px rgba(0, 0, 0, 0.25), 0 2px 10px rgba(0, 0, 0, 0.12);
  --shadow-control: 0 1px 2px rgba(0, 0, 0, 0.15);
  --highlight-inset: inset 0 1px 0 rgba(255, 255, 255, 0.4);
  --scrim: rgba(0, 0, 0, 0.32);
  --scrollbar-thumb: #d0d0d0;
  --scrollbar-thumb-hover: #b8b8b8;
}

:root[data-theme="vscode"] {
  --surface-0: var(--vscode-sideBar-background, var(--vscode-editor-background, #181818));
  --on-surface-0: var(--vscode-sideBar-foreground, var(--vscode-foreground, #e8e8e8));
  --surface-1: var(--vscode-textCodeBlock-background, var(--vscode-editorWidget-background, #232323));
  --on-surface-1: var(--vscode-foreground, #e8e8e8);
  --surface-2: var(--vscode-editorWidget-background, #202020);
  --on-surface-2: var(--vscode-editorWidget-foreground, var(--vscode-foreground, #e8e8e8));
  --surface-3: var(--vscode-menu-background, var(--vscode-editorWidget-background, #2a2a2a));
  --on-surface-3: var(--vscode-menu-foreground, var(--vscode-foreground, #e8e8e8));
  --text-1: var(--vscode-foreground, #e8e8e8);
  --text-2: var(--vscode-descriptionForeground, #a3a3a3);
  --text-3: var(--vscode-descriptionForeground, #949494);
  --input-bg: var(--vscode-input-background, #262626);
  --input-fg: var(--vscode-input-foreground, #e8e8e8);
  --input-placeholder: var(--vscode-input-placeholderForeground, var(--vscode-descriptionForeground, #949494));
  --input-border: var(--vscode-input-border, var(--vscode-widget-border, #2e2e2e));
  --button-bg: var(--vscode-button-background, #2f6fd6);
  --button-fg: var(--vscode-button-foreground, #ffffff);
  --button-hover-bg: var(--vscode-button-hoverBackground, #3a7ae0);
  --button-secondary-bg: var(--vscode-button-secondaryBackground, #2a2a2a);
  --button-secondary-fg: var(--vscode-button-secondaryForeground, #e8e8e8);
  --border: var(--vscode-panel-border, var(--vscode-widget-border, #2e2e2e));
  --border-strong: var(--vscode-widget-border, var(--vscode-panel-border, #3a3a3a));
  --focus-ring: var(--vscode-focusBorder, #6aa2f5);
  --success: var(--vscode-testing-iconPassed, #3fb950);
  --danger: var(--vscode-errorForeground, #ff6b61);
  --warning: var(--vscode-editorWarning-foreground, #d29922);
  --diff-add: var(--vscode-gitDecoration-addedResourceForeground, #3fb950);
  --diff-del: var(--vscode-gitDecoration-deletedResourceForeground, #ff6b61);
  --shadow-popover: 0 8px 24px var(--vscode-widget-shadow, rgba(0, 0, 0, 0.5));
  --shadow-control: none;
  --highlight-inset: inset 0 0 0 transparent;
  --scrim: rgba(0, 0, 0, 0.4);
  --scrollbar-thumb: var(--vscode-scrollbarSlider-background, #3a3a3a);
  --scrollbar-thumb-hover: var(--vscode-scrollbarSlider-hoverBackground, #4a4a4a);
}

:root[data-theme="hc"] {
  --surface-0: var(--vscode-sideBar-background, var(--vscode-editor-background, #000000));
  --on-surface-0: var(--vscode-sideBar-foreground, var(--vscode-foreground, #ffffff));
  --surface-1: var(--vscode-textCodeBlock-background, var(--vscode-editor-background, #000000));
  --on-surface-1: var(--vscode-foreground, #ffffff);
  --surface-2: var(--vscode-editorWidget-background, var(--vscode-editor-background, #000000));
  --on-surface-2: var(--vscode-editorWidget-foreground, var(--vscode-foreground, #ffffff));
  --surface-3: var(--vscode-menu-background, var(--vscode-editor-background, #000000));
  --on-surface-3: var(--vscode-menu-foreground, var(--vscode-foreground, #ffffff));
  --text-1: var(--vscode-foreground, #ffffff);
  --text-2: var(--vscode-foreground, #ffffff);
  --text-3: var(--vscode-descriptionForeground, #ffffff);
  --input-bg: var(--vscode-input-background, #000000);
  --input-fg: var(--vscode-input-foreground, #ffffff);
  --input-placeholder: var(--vscode-input-placeholderForeground, var(--vscode-descriptionForeground, #ffffff));
  --input-border: var(--vscode-contrastBorder, #6fc3df);
  --button-bg: var(--vscode-button-background, #000000);
  --button-fg: var(--vscode-button-foreground, #ffffff);
  --button-hover-bg: var(--vscode-button-background, #000000);
  --button-secondary-bg: var(--vscode-button-secondaryBackground, #000000);
  --button-secondary-fg: var(--vscode-button-secondaryForeground, #ffffff);
  --border: var(--vscode-contrastBorder, #6fc3df);
  --border-strong: var(--vscode-contrastBorder, #6fc3df);
  --focus-ring: var(--vscode-focusBorder, var(--vscode-contrastActiveBorder, #f38518));
  --success: var(--vscode-testing-iconPassed, #89d185);
  --danger: var(--vscode-errorForeground, #f48771);
  --warning: var(--vscode-editorWarning-foreground, #ffcc00);
  --diff-add: var(--vscode-gitDecoration-addedResourceForeground, #89d185);
  --diff-del: var(--vscode-gitDecoration-deletedResourceForeground, #f48771);
  --shadow-popover: none;
  --shadow-control: none;
  --highlight-inset: inset 0 0 0 transparent;
  --scrim: transparent;
  --scrollbar-thumb: var(--vscode-scrollbarSlider-background, #6fc3df);
  --scrollbar-thumb-hover: var(--vscode-scrollbarSlider-hoverBackground, #6fc3df);
}

/* Accents. These selectors carry no :root on purpose: an element can set its own
   data-tone/data-accent to preview another accent (the Appearance swatches).
   A plain :root falls back to the dark/blue values. */
:root,
[data-tone="dark"][data-accent="blue"] { --accent: #6aa2f5; --accent-fill: #2f6fd6; --on-accent: #ffffff; }
[data-tone="dark"][data-accent="violet"] { --accent: #a898f0; --accent-fill: #6d5bd0; --on-accent: #ffffff; }
[data-tone="dark"][data-accent="ember"] { --accent: #f0995a; --accent-fill: #a9531a; --on-accent: #ffffff; }
[data-tone="light"][data-accent="blue"] { --accent: #2563eb; --accent-fill: #2563eb; --on-accent: #ffffff; }
[data-tone="light"][data-accent="violet"] { --accent: #5b4bc4; --accent-fill: #5b4bc4; --on-accent: #ffffff; }
[data-tone="light"][data-accent="ember"] { --accent: #a9531a; --accent-fill: #a9531a; --on-accent: #ffffff; }

/* High contrast overrides the accent and switches every decorative effect off.
   Placed last, and more specific than the accent blocks, so it always wins. */
:root[data-theme="hc"][data-accent] {
  --accent: var(--vscode-textLink-foreground, var(--vscode-button-background, #3794ff));
  --accent-fill: var(--vscode-button-background, #000000);
  --on-accent: var(--vscode-button-foreground, #ffffff);
  --accent-bg: transparent;
  --accent-bg-2: transparent;
  --accent-border: var(--vscode-contrastActiveBorder, var(--vscode-contrastBorder, #f38518));
  --accent-glow: transparent;
  --success-bg: transparent;
  --danger-bg: transparent;
  --warning-bg: transparent;
  --warning-border: var(--vscode-contrastBorder, #6fc3df);
}
```

- [ ] **Step 4: Turn the old palette into aliases**

In `apps/vscode-extension/webview-ui/src/index.css`, make three changes.

First, replace the first line `@import "tailwindcss";` with:

```css
@import "tailwindcss";
@import "./styles/themes.css";
```

Second, replace the whole `@theme { … }` block (lines 3–26) with:

```css
/* Legacy names, kept as aliases of the semantic tokens in styles/themes.css so
   existing utilities and var(--color-*) references follow the active theme.
   New code reads the semantic tokens directly. */
@theme {
  --color-panel: var(--surface-0);
  --color-surface: var(--surface-1);
  --color-surface-2: var(--surface-2);
  --color-surface-3: var(--surface-3);
  --color-border: var(--border);
  --color-border-strong: var(--border-strong);
  --color-text: var(--text-1);
  --color-text-2: var(--text-2);
  --color-text-3: var(--text-3);
  --color-text-4: var(--text-3);
  --color-accent: var(--accent);
  --color-accent-deep: var(--accent-fill);
  --color-accent-hot: var(--accent-fill);
  --color-accent-ink: var(--accent);
  --color-green: var(--success);
  --color-red: var(--danger);
  --color-amber: var(--warning);
  --color-code: var(--accent);
}
```

Third, replace the whole `:root { --accent-bg: …; …; --ease-spring: …; }` block (lines 28–46) with:

```css
:root {
  /* Legacy tint names, as aliases of the derived tokens in styles/themes.css. */
  --accent-brd: var(--accent-border);
  --green-bg: var(--success-bg);
  --green-brd: color-mix(in srgb, var(--success) 25%, transparent);
  --red-bg: var(--danger-bg);
  --red-brd: color-mix(in srgb, var(--danger) 26%, transparent);
  --amber-bg: var(--warning-bg);
  --hairline: color-mix(in srgb, var(--text-1) 6%, transparent);

  /* motion tokens */
  --dur-fast: 120ms;
  --dur-base: 180ms;
  --dur-slow: 260ms;
  --ease-out: cubic-bezier(.2,.8,.3,1);
  --ease-spring: cubic-bezier(.34,1.56,.64,1);
}
```

Leave the rest of `index.css` alone in this step; Task 2 migrates it. Then in the `body` rule, change `background: var(--color-panel);` to `background: var(--surface-0);` and `color: var(--color-text);` to `color: var(--text-1);`.

- [ ] **Step 5: Run the contrast test**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/themeContrast.test.ts`
Expected: PASS. Every text/surface pair and every accent pair is ≥ 4.5, and no `var(--vscode-x)` lacks a fallback.

- [ ] **Step 6: Run the whole webview suite and the build**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run && npx tsc --noEmit && npx vite build`
Expected: all tests pass, there are no type errors, and the build succeeds. If Tailwind rejects a `var()` value inside `@theme`, change `@theme {` to `@theme inline {` and rebuild. This keeps the aliases resolving at use sites.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/styles/themes.css apps/vscode-extension/webview-ui/src/index.css apps/vscode-extension/webview-ui/src/test/themeContrast.test.ts
git commit -m "feat(webview): semantic theme tokens with dark, light, vscode and hc palettes"
```

---

### Task 2: Move the shared CSS primitives onto tokens, and the Phase 1 lint

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/index.css` (scrollbar, keyframes `breathe` and `dot-pulse`, `.hover-lift`, `.surface-card`, `.scrim`, `.accent-wash`, `.menu-item`)
- Test: `apps/vscode-extension/webview-ui/src/test/themeLint.test.ts`

**Interfaces:**
- Consumes: the tokens from Task 1.
- Produces: `LINT_SCOPE`, the array of in-scope files. Phases 2 and 3 append to it.

- [ ] **Step 1: Write the failing lint test**

Create `apps/vscode-extension/webview-ui/src/test/themeLint.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");

/** Files that must use theme tokens only. Each phase appends what it migrates
 * (spec Testing → Lint). styles/themes.css is the token definition file and is exempt. */
export const LINT_SCOPE = ["index.css", "theme.ts"];

const COLOR_LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\(/;
const TW_SIZE = /\btext-\[\d|\btext-(xs|sm|base|lg|xl|2xl)\b/;
const TW_RADIUS = /\brounded-\[\d|\brounded-(sm|md|lg|xl|2xl|3xl)\b/;
const INLINE_SIZE = /\b(fontSize|borderRadius)\s*:\s*(\d|["'`]\d)/;
const CSS_SIZE = /\b(font-size|border-radius)\s*:\s*(?!\s|var\()[^;]*\d/;
// (?!\s|var\() — without the \s, \s* can stop before the space and slip past the lookahead,
// flagging tokenized values like `font-size: var(--vscode-font-size, 13px)`.

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
```

`theme.ts` doesn't exist until Task 3. Until then, temporarily set `LINT_SCOPE = ["index.css"]` in this task, and Task 3 adds `"theme.ts"`.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/themeLint.test.ts`
Expected: FAIL. It lists the `index.css` lines that use `rgba(…)`, `border-radius: 4px` and `border-radius: 12px`.

- [ ] **Step 3: Migrate the primitives**

In `apps/vscode-extension/webview-ui/src/index.css`:

Replace the scrollbar thumb rules with:

```css
::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border-radius: var(--r-control);
  border: 2px solid var(--surface-0);
}

::-webkit-scrollbar-thumb:hover {
  background: var(--scrollbar-thumb-hover);
}
```

Replace `@keyframes breathe` with:

```css
@keyframes breathe {
  0%, 100% { box-shadow: 0 0 24px var(--accent-glow), var(--highlight-inset); }
  50% { box-shadow: 0 0 44px var(--accent-bg-2), var(--highlight-inset); }
}
```

Replace `@keyframes dot-pulse` with:

```css
@keyframes dot-pulse {
  0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--warning) 45%, transparent); }
  50% { box-shadow: 0 0 0 4px color-mix(in srgb, var(--warning) 0%, transparent); }
}
```

In `.hover-lift:hover`, replace the `box-shadow` line with:

```css
  box-shadow: var(--shadow-control), 0 0 20px var(--accent-glow);
```

Replace `.surface-card`, `.scrim` and `.accent-wash` with:

```css
.surface-card {
  background: var(--surface-2);
  color: var(--on-surface-2);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-popover);
}

.scrim {
  background: var(--scrim);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}

.accent-wash {
  background:
    linear-gradient(180deg, var(--accent-bg), transparent),
    var(--surface-3);
}
```

In `.menu-item:hover`, replace `color: var(--color-accent-ink);` with `color: var(--accent);`, and the `box-shadow` with:

```css
  box-shadow: var(--shadow-control), 0 0 16px -6px var(--accent-glow);
```

In `.menu-item:active`, replace the `box-shadow` with `box-shadow: var(--shadow-control);`.

Update the "DESIGN LANGUAGE" header comment's first line to read `DESIGN LANGUAGE — shared primitives. Colours come from styles/themes.css.`

- [ ] **Step 4: Run the lint and contrast tests**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/themeLint.test.ts src/test/themeContrast.test.ts`
Expected: PASS.

- [ ] **Step 5: Run the full webview suite and build**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run && npx vite build`
Expected: PASS and the build succeeds.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/index.css apps/vscode-extension/webview-ui/src/test/themeLint.test.ts
git commit -m "refactor(webview): shared primitives read theme tokens; add theme lint"
```

---

### Task 3: Theme resolution in the webview

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/theme.ts`
- Modify: `apps/vscode-extension/webview-ui/src/main.tsx`, `src/settings/main.tsx`, `src/setup/main.tsx`, `src/memory/main.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/test/themeLint.test.ts` (add `"theme.ts"` to `LINT_SCOPE`)
- Test: `apps/vscode-extension/webview-ui/src/test/theme.test.ts`, and a case in `src/test/useAppState.test.ts`

**Interfaces:**
- Produces, in `webview-ui/src/theme.ts`:
  - `APPEARANCES = ["vscode","dark","light"] as const`, `ACCENTS = ["blue","violet","ember"] as const`
  - `type Appearance`, `type Accent`, `type ThemeName = Appearance | "hc"`, `type Tone = "dark" | "light"`
  - `interface ResolvedTheme { theme: ThemeName; tone: Tone; accent: Accent }`
  - `isAppearance(v: unknown): v is Appearance`, `isAccent(v: unknown): v is Accent`
  - `resolveTheme(appearance: Appearance, accent: Accent, body: { contains(cls: string): boolean }): ResolvedTheme`
  - `applyTheme(root: HTMLElement, t: ResolvedTheme): void`
  - `installThemeSync(doc?: Document, win?: Window): () => void`
- The message it consumes: `{ type: "themeChanged", appearance: Appearance, accent: Accent }` (sent by Task 4).

- [ ] **Step 1: Write the failing tests**

Create `apps/vscode-extension/webview-ui/src/test/theme.test.ts`:

```ts
import { afterEach, describe, expect, it } from "vitest";
import { applyTheme, installThemeSync, resolveTheme } from "../theme";

const classes = (...names: string[]) => ({ contains: (c: string) => names.includes(c) });

describe("resolveTheme", () => {
  it("uses the configured appearance outside high contrast", () => {
    expect(resolveTheme("dark", "violet", classes("vscode-light"))).toEqual({ theme: "dark", tone: "dark", accent: "violet" });
    expect(resolveTheme("light", "blue", classes("vscode-dark"))).toEqual({ theme: "light", tone: "light", accent: "blue" });
  });

  it("takes the tone from the editor in vscode mode", () => {
    expect(resolveTheme("vscode", "blue", classes("vscode-light"))).toEqual({ theme: "vscode", tone: "light", accent: "blue" });
    expect(resolveTheme("vscode", "ember", classes("vscode-dark"))).toEqual({ theme: "vscode", tone: "dark", accent: "ember" });
  });

  it("resolves high contrast over any appearance and accent", () => {
    expect(resolveTheme("light", "ember", classes("vscode-high-contrast")).theme).toBe("hc");
    const hcLight = resolveTheme("dark", "violet", classes("vscode-high-contrast-light"));
    expect(hcLight).toEqual({ theme: "hc", tone: "light", accent: "violet" });
  });
});

describe("applyTheme and installThemeSync", () => {
  let dispose: (() => void) | null = null;
  afterEach(() => {
    dispose?.();
    dispose = null;
    document.body.className = "";
    for (const a of ["appearance", "accent", "theme", "tone"]) delete document.documentElement.dataset[a];
  });

  it("writes the three attributes on the root", () => {
    applyTheme(document.documentElement, { theme: "light", tone: "light", accent: "ember" });
    const d = document.documentElement.dataset;
    expect([d.theme, d.tone, d.accent]).toEqual(["light", "light", "ember"]);
  });

  it("reads host-stamped settings, defaulting to vscode/blue", () => {
    document.body.className = "vscode-dark";
    dispose = installThemeSync();
    expect(document.documentElement.dataset.theme).toBe("vscode");
    expect(document.documentElement.dataset.accent).toBe("blue");
  });

  it("re-resolves when the editor switches to high contrast", async () => {
    document.documentElement.dataset.appearance = "dark";
    document.body.className = "vscode-dark";
    dispose = installThemeSync();
    expect(document.documentElement.dataset.theme).toBe("dark");
    document.body.className = "vscode-high-contrast-light";
    await Promise.resolve(); // MutationObserver callbacks run as microtasks
    expect(document.documentElement.dataset.theme).toBe("hc");
    expect(document.documentElement.dataset.tone).toBe("light");
  });

  it("applies a themeChanged message and ignores malformed ones", () => {
    document.body.className = "vscode-dark";
    dispose = installThemeSync();
    window.dispatchEvent(new MessageEvent("message", { data: { type: "themeChanged", appearance: "light", accent: "violet" } }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.dataset.accent).toBe("violet");
    window.dispatchEvent(new MessageEvent("message", { data: { type: "themeChanged", appearance: "neon", accent: "pink" } }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
```

Then add this case inside the `describe("useAppState", …)` block of `apps/vscode-extension/webview-ui/src/test/useAppState.test.ts`. The file already imports `renderHook` and `act`, and reads `result.current.state`:

```ts
it("ignores a themeChanged message (Phase 1 theming)", () => {
  const { result } = renderHook(() => useAppState());
  const before = result.current.state;
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data: { type: "themeChanged", appearance: "light", accent: "blue" } }));
  });
  expect(result.current.state).toEqual(before);
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/theme.test.ts`
Expected: FAIL with `Cannot find module '../theme'`.

- [ ] **Step 3: Implement `theme.ts`**

Create `apps/vscode-extension/webview-ui/src/theme.ts`:

```ts
/** Theme resolution (spec 2026-10-13 §1). The host stamps data-appearance and
 * data-accent on <html> and sends themeChanged when the settings change; VS Code
 * sets vscode-light|vscode-dark|vscode-high-contrast|vscode-high-contrast-light on
 * <body> and swaps it live when the user changes editor theme. styles/themes.css
 * keys every token on the data-theme, data-tone and data-accent written here. */

export const APPEARANCES = ["vscode", "dark", "light"] as const;
export const ACCENTS = ["blue", "violet", "ember"] as const;
export type Appearance = (typeof APPEARANCES)[number];
export type Accent = (typeof ACCENTS)[number];
export type ThemeName = Appearance | "hc";
export type Tone = "dark" | "light";

export interface ResolvedTheme {
  theme: ThemeName;
  tone: Tone;
  accent: Accent;
}

interface ClassList {
  contains(cls: string): boolean;
}

export function isAppearance(v: unknown): v is Appearance {
  return typeof v === "string" && (APPEARANCES as readonly string[]).includes(v);
}

export function isAccent(v: unknown): v is Accent {
  return typeof v === "string" && (ACCENTS as readonly string[]).includes(v);
}

export function resolveTheme(appearance: Appearance, accent: Accent, body: ClassList): ResolvedTheme {
  // High contrast is an accessibility choice; it overrides the user's appearance and accent.
  if (body.contains("vscode-high-contrast-light")) return { theme: "hc", tone: "light", accent };
  if (body.contains("vscode-high-contrast")) return { theme: "hc", tone: "dark", accent };
  if (appearance === "vscode") {
    return { theme: "vscode", tone: body.contains("vscode-light") ? "light" : "dark", accent };
  }
  return { theme: appearance, tone: appearance, accent };
}

export function applyTheme(root: HTMLElement, t: ResolvedTheme): void {
  root.dataset.theme = t.theme;
  root.dataset.tone = t.tone;
  root.dataset.accent = t.accent;
}

function configured(root: HTMLElement): { appearance: Appearance; accent: Accent } {
  const appearance = root.dataset.appearance;
  const accent = root.dataset.accent;
  return {
    appearance: isAppearance(appearance) ? appearance : "vscode",
    accent: isAccent(accent) ? accent : "blue",
  };
}

/** Resolve now, then keep the root in sync with body-class changes and
 * themeChanged messages. Returns a disposer (used by tests). */
export function installThemeSync(doc: Document = document, win: Window = window): () => void {
  const root = doc.documentElement;
  let current = configured(root);
  const apply = () => applyTheme(root, resolveTheme(current.appearance, current.accent, doc.body.classList));
  apply();

  const observer = new MutationObserver(apply);
  observer.observe(doc.body, { attributes: true, attributeFilter: ["class"] });

  const onMessage = (event: MessageEvent) => {
    const m = event.data as { type?: unknown; appearance?: unknown; accent?: unknown } | null;
    if (!m || m.type !== "themeChanged" || !isAppearance(m.appearance) || !isAccent(m.accent)) return;
    current = { appearance: m.appearance, accent: m.accent };
    root.dataset.appearance = m.appearance;
    apply();
  };
  win.addEventListener("message", onMessage);

  return () => {
    observer.disconnect();
    win.removeEventListener("message", onMessage);
  };
}
```

- [ ] **Step 4: Install it in the four entries**

In each of `webview-ui/src/main.tsx`, `src/settings/main.tsx`, `src/setup/main.tsx` and `src/memory/main.tsx`, add the import next to the existing imports and call it **before** `ReactDOM.createRoot(…)`.

For `src/main.tsx`:

```tsx
import { installThemeSync } from "./theme";
// …existing imports…

installThemeSync();

ReactDOM.createRoot(document.getElementById("root")!).render(
```

For the three sub-entries, use `import { installThemeSync } from "../theme";` instead.

Then set `LINT_SCOPE = ["index.css", "theme.ts"]` in `src/test/themeLint.test.ts`.

- [ ] **Step 5: Run the tests**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/theme.test.ts src/test/useAppState.test.ts src/test/themeLint.test.ts`
Expected: PASS.

- [ ] **Step 6: Run the full suite, typecheck and build**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run && npx tsc --noEmit && npx vite build`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/theme.ts apps/vscode-extension/webview-ui/src/main.tsx apps/vscode-extension/webview-ui/src/settings/main.tsx apps/vscode-extension/webview-ui/src/setup/main.tsx apps/vscode-extension/webview-ui/src/memory/main.tsx apps/vscode-extension/webview-ui/src/test/theme.test.ts apps/vscode-extension/webview-ui/src/test/useAppState.test.ts apps/vscode-extension/webview-ui/src/test/themeLint.test.ts
git commit -m "feat(webview): resolve theme from settings and VS Code body classes, live"
```

---

### Task 4: Host settings, attribute injection and live broadcast

**Files:**
- Create: `apps/vscode-extension/src/theme-settings.ts`
- Modify: `apps/vscode-extension/package.json` (`contributes.configuration.properties`)
- Modify: `apps/vscode-extension/src/settings.ts` (add `readThemeConfig`)
- Modify: `apps/vscode-extension/src/chat-panel.ts`, `src/settings-panel.ts`, `src/setup-panel.ts`, `src/memory-panel.ts`
- Modify: `apps/vscode-extension/src/extension.ts`
- Modify: `apps/vscode-extension/test/model-auth-refresh.test.ts` (bind the new names)
- Test: `apps/vscode-extension/test/theme-settings.test.ts`

**Interfaces:**
- Consumes: the attribute names and the `themeChanged` shape from Task 3.
- Produces, in `src/theme-settings.ts`:
  - `APPEARANCES`, `ACCENTS`, `type Appearance`, `type Accent`
  - `interface ThemeSettings { appearance: Appearance; accent: Accent }`, and `DEFAULT_THEME`
  - `normalizeThemeSettings(raw: { appearance?: unknown; accent?: unknown }): ThemeSettings`
  - `injectThemeAttributes(html: string, s: ThemeSettings): string`
  - `type ThemeMessage = { type: "themeChanged" } & ThemeSettings`
  - `class ThemeHub { get settings(): ThemeSettings; update(s: ThemeSettings): void; subscribe(post: (m: ThemeMessage) => void): () => void }`, and `export const themeHub`
- Produces, in `src/settings.ts`: `readThemeConfig(): ThemeSettings`. Task 5 uses it.

- [ ] **Step 1: Write the failing host tests**

Create `apps/vscode-extension/test/theme-settings.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  ACCENTS, APPEARANCES, DEFAULT_THEME, ThemeHub, injectThemeAttributes, normalizeThemeSettings,
} from "../src/theme-settings.js";

describe("normalizeThemeSettings", () => {
  it("keeps valid values", () => {
    expect(normalizeThemeSettings({ appearance: "light", accent: "ember" })).toEqual({ appearance: "light", accent: "ember" });
  });
  it("falls back to defaults for unknown or missing values", () => {
    expect(normalizeThemeSettings({ appearance: "neon", accent: "pink" })).toEqual(DEFAULT_THEME);
    expect(normalizeThemeSettings({})).toEqual({ appearance: "vscode", accent: "blue" });
  });
});

describe("injectThemeAttributes", () => {
  it("stamps appearance, accent, theme and tone on the html tag", () => {
    const out = injectThemeAttributes('<!DOCTYPE html>\n<html lang="en">\n<head>', { appearance: "light", accent: "violet" });
    expect(out).toContain('<html lang="en" data-appearance="light" data-accent="violet" data-theme="light" data-tone="light">');
  });
  it("uses a dark tone as the first-paint guess in vscode mode", () => {
    const out = injectThemeAttributes("<html>", { appearance: "vscode", accent: "blue" });
    expect(out).toBe('<html data-appearance="vscode" data-accent="blue" data-theme="vscode" data-tone="dark">');
  });
});

describe("ThemeHub", () => {
  it("broadcasts changes to subscribers and stops after unsubscribe", () => {
    const hub = new ThemeHub();
    const post = vi.fn();
    const unsubscribe = hub.subscribe(post);
    hub.update({ appearance: "dark", accent: "ember" });
    expect(post).toHaveBeenCalledWith({ type: "themeChanged", appearance: "dark", accent: "ember" });
    unsubscribe();
    hub.update({ appearance: "light", accent: "ember" });
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("does not broadcast an unchanged value", () => {
    const hub = new ThemeHub();
    const post = vi.fn();
    hub.subscribe(post);
    hub.update(DEFAULT_THEME);
    expect(post).not.toHaveBeenCalled();
    expect(hub.settings).toEqual(DEFAULT_THEME);
  });
});

// Drift guard: the webview keeps its own copy of these lists (separate bundle).
describe("host and webview theme lists match", () => {
  const webview = readFileSync(join(__dirname, "..", "webview-ui", "src", "theme.ts"), "utf8");
  const list = (name: string) =>
    JSON.parse(webview.match(new RegExp(`${name} = (\\[[^\\]]*\\]) as const`))![1]!.replace(/'/g, '"'));
  it("appearances", () => expect(list("APPEARANCES")).toEqual([...APPEARANCES]));
  it("accents", () => expect(list("ACCENTS")).toEqual([...ACCENTS]));
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/vscode-extension && npx vitest run test/theme-settings.test.ts`
Expected: FAIL with `Cannot find module '../src/theme-settings.js'`.

- [ ] **Step 3: Implement `src/theme-settings.ts`**

```ts
// vscode-free theme settings (spec 2026-10-13 §1). The webview mirrors these lists
// in webview-ui/src/theme.ts; test/theme-settings.test.ts guards the drift.

export const APPEARANCES = ["vscode", "dark", "light"] as const;
export const ACCENTS = ["blue", "violet", "ember"] as const;
export type Appearance = (typeof APPEARANCES)[number];
export type Accent = (typeof ACCENTS)[number];

export interface ThemeSettings {
  appearance: Appearance;
  accent: Accent;
}

export const DEFAULT_THEME: ThemeSettings = { appearance: "vscode", accent: "blue" };

export type ThemeMessage = { type: "themeChanged" } & ThemeSettings;

export function normalizeThemeSettings(raw: { appearance?: unknown; accent?: unknown }): ThemeSettings {
  const appearance = (APPEARANCES as readonly unknown[]).includes(raw.appearance)
    ? (raw.appearance as Appearance) : DEFAULT_THEME.appearance;
  const accent = (ACCENTS as readonly unknown[]).includes(raw.accent)
    ? (raw.accent as Accent) : DEFAULT_THEME.accent;
  return { appearance, accent };
}

/** Stamp the configured theme on <html> so the first paint is right; the webview's
 * theme.ts refines data-theme and data-tone from VS Code's body classes once it runs
 * (vscode mode's real tone and high contrast are only known there). Values come from
 * normalizeThemeSettings, so they are always one of the known literals. */
export function injectThemeAttributes(html: string, s: ThemeSettings): string {
  const tone = s.appearance === "light" ? "light" : "dark";
  const attrs = `data-appearance="${s.appearance}" data-accent="${s.accent}" data-theme="${s.appearance}" data-tone="${tone}"`;
  return html.replace(/<html(\s[^>]*)?>/, (_m, existing: string | undefined) => `<html${existing ?? ""} ${attrs}>`);
}

export class ThemeHub {
  private current: ThemeSettings = DEFAULT_THEME;
  private readonly listeners = new Set<(m: ThemeMessage) => void>();

  get settings(): ThemeSettings {
    return this.current;
  }

  update(next: ThemeSettings): void {
    if (next.appearance === this.current.appearance && next.accent === this.current.accent) return;
    this.current = next;
    const message: ThemeMessage = { type: "themeChanged", ...next };
    for (const post of this.listeners) post(message);
  }

  subscribe(post: (m: ThemeMessage) => void): () => void {
    this.listeners.add(post);
    return () => {
      this.listeners.delete(post);
    };
  }
}

/** One hub per extension host; every Crucible panel subscribes on creation. */
export const themeHub = new ThemeHub();
```

- [ ] **Step 4: Run the host tests**

Run: `cd apps/vscode-extension && npx vitest run test/theme-settings.test.ts`
Expected: PASS.

- [ ] **Step 5: Contribute the settings**

In `apps/vscode-extension/package.json`, inside `contributes.configuration.properties`, add after `"crucible.memory.reranker": {…}` (insert a comma after its closing brace):

```json
        "crucible.appearance": {
          "type": "string",
          "enum": ["vscode", "dark", "light"],
          "enumDescriptions": [
            "Match the current VS Code colour theme",
            "Crucible Dark",
            "Crucible Light"
          ],
          "default": "vscode",
          "scope": "application",
          "description": "Colours for Crucible's panels. High-contrast VS Code themes always win."
        },
        "crucible.accent": {
          "type": "string",
          "enum": ["blue", "violet", "ember"],
          "default": "blue",
          "scope": "application",
          "description": "Accent colour for Crucible's panels (links, your messages, the send button)."
        }
```

- [ ] **Step 6: Read the settings, and seed and refresh the hub**

In `apps/vscode-extension/src/settings.ts`, add:

```ts
import { normalizeThemeSettings, type ThemeSettings } from "./theme-settings.js";

/** crucible.appearance + crucible.accent, normalized (unknown values → defaults). */
export function readThemeConfig(): ThemeSettings {
  const cfg = vscode.workspace.getConfiguration("crucible");
  return normalizeThemeSettings({ appearance: cfg.get("appearance"), accent: cfg.get("accent") });
}
```

In `apps/vscode-extension/src/extension.ts`, near the top of `activate` (before any panel is constructed), add:

```ts
  themeHub.update(readThemeConfig());
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("crucible.appearance") || event.affectsConfiguration("crucible.accent")) {
        themeHub.update(readThemeConfig());
      }
    }),
  );
```

with the imports `import { themeHub } from "./theme-settings.js";` and `readThemeConfig` added to the existing `./settings.js` import.

- [ ] **Step 7: Inject the attributes and subscribe in the four panels**

In each of `src/chat-panel.ts`, `src/settings-panel.ts`, `src/setup-panel.ts` and `src/memory-panel.ts`, make three changes:

1. Import `import { injectThemeAttributes, themeHub } from "./theme-settings.js";`.
2. In `buildHtml()`, change the final `return html;` to `return injectThemeAttributes(html, themeHub.settings);`. Leave the "build is missing" fallback HTML unchanged.
3. Add the field `private themeSubscription: (() => void) | null = null;`. Right after every `this.panel.webview.html = this.buildHtml();` (this includes `ChatPanel.reattach`, the restored-panel path), add:

```ts
    this.themeSubscription?.();
    this.themeSubscription = themeHub.subscribe((message) => {
      void this.panel?.webview.postMessage(message);
    });
```

In each panel's existing `onDidDispose` handler, add before `this.panel = null;`:

```ts
      this.themeSubscription?.();
      this.themeSubscription = null;
```

(`ChatPanel` registers its dispose handler inside `registerHandlers()`; find it with `grep -n "onDidDispose" src/chat-panel.ts`.)

`test/model-auth-refresh.test.ts` runs `SettingsPanel`'s source text through `new Function` with explicit bindings, so it must also bind the two new names. In its `"forwards the standalone panel callback to its real handler factory"` case, add to the `execute(…)` bindings, after `createSettingsHandler, exports: {},`:

```ts
      // The panel subscribes to theme changes in open() (theming Phase 1).
      themeHub: { settings: { appearance: "vscode", accent: "blue" }, subscribe: () => () => {} },
      injectThemeAttributes: (html: string) => html,
```

- [ ] **Step 8: Typecheck and run all host tests**

Run: `cd apps/vscode-extension && npx tsc -p tsconfig.json --noEmit && npx vitest run`
Expected: no type errors. All tests pass **except** the three `model-auth-refresh.test.ts` › "host catalog and auth refresh" cases (`claudeCatalogEpoch is not defined`), which already fail on `main` at `437fa77` and are not this plan's to fix.

- [ ] **Step 9: Commit**

```bash
git add apps/vscode-extension/src/theme-settings.ts apps/vscode-extension/src/settings.ts apps/vscode-extension/src/extension.ts apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/src/settings-panel.ts apps/vscode-extension/src/setup-panel.ts apps/vscode-extension/src/memory-panel.ts apps/vscode-extension/package.json apps/vscode-extension/test/theme-settings.test.ts
git commit -m "feat(extension): crucible.appearance and crucible.accent, stamped into panels and broadcast live"
```

---

### Task 5: Settings › Appearance, and a Settings shell that works offline

**Files:**
- Modify: `apps/vscode-extension/src/settings-sections.ts`, `test/settings-sections.test.ts`
- Modify: `apps/vscode-extension/src/settings-data.ts` (message types, `SettingsDeps`, handler), `src/settings-deps.ts`, `test/settings-data.test.ts`
- Modify: `apps/vscode-extension/webview-ui/src/settings/types.ts`, `settings/sections/meta.ts`, `settings/SettingsApp.tsx`
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/AppearanceSection.tsx`, `…/AppearanceSection.test.tsx`
- Test: a new offline case in `apps/vscode-extension/webview-ui/src/settings/SettingsApp.test.tsx`

**Interfaces:**
- Consumes: `ThemeSettings`, `normalizeThemeSettings`, `APPEARANCES` and `ACCENTS` (Task 4); `readThemeConfig()` (Task 4).
- Produces:
  - In `SettingsInMsg` (host and webview): `{ type: "settings/setAppearance"; appearance: string; accent: string }`
  - In `SettingsOutMsg` (host and webview): `{ type: "settings/localState"; appearance: Appearance; accent: Accent }`
  - In `SettingsDeps`: `readAppearance(): ThemeSettings; setAppearance(s: ThemeSettings): Promise<void>`
  - The section id `"appearance"`, placed second in the nav after `"overview"`.

- [ ] **Step 1: Write the failing host tests**

In `apps/vscode-extension/test/settings-sections.test.ts`, change the expected id list to:

```ts
    expect(SETTINGS_SECTIONS.map((s) => s.id)).toEqual([
      "overview",
      "appearance",
      "provider",
      "mcp",
      "skills",
      "agents",
      "instructions",
      "policies",
      "runtime",
    ]);
```

and rename that test to `"lists the nine settings sections in nav order, each with a label"`.

In `apps/vscode-extension/test/settings-data.test.ts`, make two changes. First, in the `deps()` factory's returned object, add:

```ts
    readAppearance: () => ({ appearance: "vscode", accent: "blue" }),
    setAppearance: vi.fn(async () => {}),
```

Then append:

```ts
describe("appearance (spec 2026-10-13 §1)", () => {
  it("posts local appearance state before the backend snapshot on load", async () => {
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(deps(), (m) => posted.push(m));
    await handle({ type: "settings/load" });
    expect(posted[0]).toEqual({ type: "settings/localState", appearance: "vscode", accent: "blue" });
    expect(posted[1]?.type).toBe("settings/state");
  });

  it("still posts local state when the backend is unreachable", async () => {
    const posted: SettingsOutMsg[] = [];
    const d = deps();
    d.client.getConfig = async () => { throw new Error("connect ECONNREFUSED"); };
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({ type: "settings/load" });
    expect(posted.map((m) => m.type)).toEqual(["settings/localState", "settings/error"]);
  });

  it("writes a valid appearance and echoes the new local state", async () => {
    const posted: SettingsOutMsg[] = [];
    const d = deps();
    d.readAppearance = () => ({ appearance: "light", accent: "ember" });
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({ type: "settings/setAppearance", appearance: "light", accent: "ember" });
    expect(d.setAppearance).toHaveBeenCalledWith({ appearance: "light", accent: "ember" });
    expect(posted.at(-1)).toEqual({ type: "settings/localState", appearance: "light", accent: "ember" });
  });

  it("rejects an unknown appearance without writing", async () => {
    const posted: SettingsOutMsg[] = [];
    const d = deps();
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({ type: "settings/setAppearance", appearance: "neon", accent: "blue" });
    expect(d.setAppearance).not.toHaveBeenCalled();
    expect(posted.at(-1)).toEqual({ type: "settings/error", message: "Unknown appearance or accent: neon / blue" });
  });
});
```

Also update the existing `"load posts a full state snapshot"` case in that file: `settings/localState` is now posted before the snapshot, so it must find the snapshot instead of reading `posted[0]`:

```ts
    await createSettingsHandler(deps(), (m) => posted.push(m))({ type: "settings/load" });
    // settings/localState is posted first (appearance needs no backend); find the snapshot.
    const state = stateMsg(posted.find((m) => m.type === "settings/state")!).state;
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/vscode-extension && npx vitest run test/settings-sections.test.ts test/settings-data.test.ts`
Expected: FAIL. The section list is missing `appearance`, and the handler has no local-state or appearance handling.

- [ ] **Step 3: Implement the host side**

In `src/settings-sections.ts`, add `| "appearance"` to `SettingsSectionId`, and insert `{ id: "appearance", label: "Appearance" },` as the second entry of `SETTINGS_SECTIONS`.

In `src/settings-data.ts`, make four changes:

1. Add `import { APPEARANCES, ACCENTS, type Accent, type Appearance, type ThemeSettings } from "./theme-settings.js";`.
2. Add `| { type: "settings/setAppearance"; appearance: string; accent: string }` to `SettingsInMsg`, and `| { type: "settings/localState"; appearance: Appearance; accent: Accent }` to `SettingsOutMsg`.
3. Add these two members to `SettingsDeps`:

```ts
  /** crucible.appearance + crucible.accent. Local VS Code settings — no backend needed. */
  readAppearance(): ThemeSettings;
  setAppearance(settings: ThemeSettings): Promise<void>;
```

4. In the handler's `switch`, replace the `"settings/load"` case and add a new case:

```ts
        case "settings/load": {
          // Local first: the Appearance section and the nav render even when the
          // backend is down (spec §1 — Settings must not blank on a backend failure).
          post({ type: "settings/localState", ...deps.readAppearance() });
          await postState();
          return;
        }
        case "settings/setAppearance": {
          const appearance = APPEARANCES.find((a) => a === msg.appearance);
          const accent = ACCENTS.find((a) => a === msg.accent);
          if (!appearance || !accent) {
            post({ type: "settings/error", message: `Unknown appearance or accent: ${msg.appearance} / ${msg.accent}` });
            return;
          }
          await deps.setAppearance({ appearance, accent });
          post({ type: "settings/localState", ...deps.readAppearance() });
          return;
        }
```

Check that the handler's existing `catch` posts `settings/error` when `postState()` throws. The offline test asserts exactly that order.

In `src/settings-deps.ts`, add `import { readThemeConfig } from "./settings.js";`, and add to the returned deps object:

```ts
    readAppearance: () => readThemeConfig(),
    setAppearance: async ({ appearance, accent }) => {
      const cfg = vscode.workspace.getConfiguration("crucible");
      await cfg.update("appearance", appearance, vscode.ConfigurationTarget.Global);
      await cfg.update("accent", accent, vscode.ConfigurationTarget.Global);
    },
```

- [ ] **Step 4: Run the host tests**

Run: `cd apps/vscode-extension && npx vitest run test/settings-sections.test.ts test/settings-data.test.ts && npx tsc -p tsconfig.json --noEmit`
Expected: PASS, with no type errors.

- [ ] **Step 5: Write the failing webview tests**

Create `apps/vscode-extension/webview-ui/src/settings/sections/AppearanceSection.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppearanceSection } from "./AppearanceSection";

describe("AppearanceSection", () => {
  it("shows the current appearance and accent as checked radios", () => {
    render(<AppearanceSection appearance="vscode" accent="blue" busy={false} send={vi.fn()} />);
    expect(screen.getByRole("radio", { name: /Match VS Code/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Blue/ })).toBeChecked();
  });

  it("posts settings/setAppearance with both values when either changes", () => {
    const send = vi.fn();
    render(<AppearanceSection appearance="vscode" accent="blue" busy={false} send={send} />);
    fireEvent.click(screen.getByRole("radio", { name: /Crucible Light/ }));
    expect(send).toHaveBeenCalledWith({ type: "settings/setAppearance", appearance: "light", accent: "blue" });
    fireEvent.click(screen.getByRole("radio", { name: /Ember/ }));
    expect(send).toHaveBeenLastCalledWith({ type: "settings/setAppearance", appearance: "vscode", accent: "ember" });
  });

  it("explains that high contrast overrides these choices", () => {
    render(<AppearanceSection appearance="dark" accent="violet" busy={false} send={vi.fn()} />);
    expect(screen.getByText(/High-contrast VS Code themes always win/)).toBeTruthy();
  });
});
```

Append to `apps/vscode-extension/webview-ui/src/settings/SettingsApp.test.tsx`:

```tsx
describe("SettingsApp offline (backend unreachable)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders the nav and Appearance from local state; other sections offer a retry", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/localState", appearance: "vscode", accent: "blue" });
    deliver({ type: "settings/error", message: "connect ECONNREFUSED" });
    navClick(/Appearance/);
    expect(screen.getByRole("heading", { name: "Appearance" })).toBeTruthy();
    navClick(/Provider/);
    expect(screen.getByText(/connect ECONNREFUSED/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(vscode.postMessage).toHaveBeenLastCalledWith({ type: "settings/load" });
  });
});
```

- [ ] **Step 6: Run them to verify they fail**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/settings`
Expected: FAIL. The `AppearanceSection` module is missing and the offline shell doesn't render.

- [ ] **Step 7: Implement the webview side**

In `webview-ui/src/settings/types.ts`, add `import type { Accent, Appearance } from "../theme";`. Then add `| { type: "settings/setAppearance"; appearance: Appearance; accent: Accent }` to `SettingsInMsg`, and `| { type: "settings/localState"; appearance: Appearance; accent: Accent }` to `SettingsOutMsg`.

In `webview-ui/src/settings/sections/meta.ts`, add `| "appearance"` to `SectionId`, and insert as the **first** entry of `SECTIONS` (Overview isn't in `SECTIONS`, so this puts it second in the nav):

```ts
  { id: "appearance", label: "Appearance", icon: "orbit", blurb: "Theme and accent colour for every Crucible panel.", tint: "var(--color-accent)" },
```

Create `webview-ui/src/settings/sections/AppearanceSection.tsx`:

```tsx
import type { Accent, Appearance } from "../../theme";
import type { SettingsInMsg } from "../types";
import { SectionHeader } from "../SectionHeader";

const APPEARANCE_OPTIONS: { value: Appearance; label: string; hint: string }[] = [
  { value: "vscode", label: "Match VS Code", hint: "Uses your editor's colour theme." },
  { value: "dark", label: "Crucible Dark", hint: "Neutral greys, any editor theme." },
  { value: "light", label: "Crucible Light", hint: "White and light greys." },
];

const ACCENT_OPTIONS: { value: Accent; label: string }[] = [
  { value: "blue", label: "Blue" },
  { value: "violet", label: "Violet" },
  { value: "ember", label: "Ember" },
];

interface Props {
  appearance: Appearance;
  accent: Accent;
  busy: boolean;
  send: (msg: SettingsInMsg) => void;
}

/** Spec 2026-10-13 §1. Changes write the VS Code settings through the host, which
 * broadcasts themeChanged, so every panel (this one included) re-themes live. */
export function AppearanceSection({ appearance, accent, busy, send }: Props) {
  const choose = (next: { appearance?: Appearance; accent?: Accent }) =>
    send({ type: "settings/setAppearance", appearance: next.appearance ?? appearance, accent: next.accent ?? accent });
  // Swatches preview each accent in the current tone by scoping data-tone/data-accent
  // on the swatch itself (styles/themes.css accent blocks aren't :root-only).
  const tone = document.documentElement.dataset.tone === "light" ? "light" : "dark";

  return (
    <div className="grid gap-5">
      <SectionHeader title="Appearance" description="Colours for Crucible's panels. High-contrast VS Code themes always win." />
      <fieldset className="grid gap-2" disabled={busy}>
        <legend className="mb-1 text-text-2" style={{ fontSize: "var(--fs-meta)" }}>Theme</legend>
        {APPEARANCE_OPTIONS.map((o) => (
          <label key={o.value} className="flex cursor-pointer items-start gap-2">
            <input type="radio" name="appearance" value={o.value} checked={appearance === o.value}
              onChange={() => choose({ appearance: o.value })} />
            <span>
              <span className="block text-text">{o.label}</span>
              <span className="block text-text-3" style={{ fontSize: "var(--fs-meta)" }}>{o.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <fieldset className="grid gap-2" disabled={busy}>
        <legend className="mb-1 text-text-2" style={{ fontSize: "var(--fs-meta)" }}>Accent</legend>
        <div className="flex flex-wrap gap-4">
          {ACCENT_OPTIONS.map((o) => (
            <label key={o.value} className="flex cursor-pointer items-center gap-2">
              <input type="radio" name="accent" value={o.value} checked={accent === o.value}
                onChange={() => choose({ accent: o.value })} />
              <span aria-hidden="true" data-tone={tone} data-accent={o.value}
                style={{ width: 14, height: 14, borderRadius: "var(--r-round)", background: "var(--accent-fill)" }} />
              <span className="text-text">{o.label}</span>
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  );
}
```

`SectionHeader` (`settings/SectionHeader.tsx:20`) takes `{ title, description, search?, action? }` and renders the title as an `<h1>`, which is what the tests' `getByRole("heading", { name: "Appearance" })` finds.

In `webview-ui/src/settings/SettingsApp.tsx`, make six changes:

1. Add `import { AppearanceSection } from "./sections/AppearanceSection";` and `import type { Accent, Appearance } from "../theme";`.
2. Add state: `const [local, setLocal] = useState<{ appearance: Appearance; accent: Accent } | null>(null);`.
3. In `onMessage`, add the branch `else if (msg.type === "settings/localState") { setLocal({ appearance: msg.appearance, accent: msg.accent }); }`.
4. Change the early return guard from `if (!state) {` to `if (!state && !local) {`. The existing loading and error screens stay for the moment before anything has arrived.
5. Compute the shared props only when `state` exists: replace `const props: SectionProps = { state, busy, send };` with `const props: SectionProps | null = state ? { state, busy, send } : null;`, and use `state?.mcp.servers.length` and `state?.skills.length` in `counts`.
6. Gate the error banner and the restart banner on `state &&`. Then replace the sections block with:

```tsx
          <div key={section} className="anim-section">
            {section === "appearance" && local && (
              <AppearanceSection appearance={local.appearance} accent={local.accent} busy={busy} send={send} />
            )}
            {section !== "appearance" && !props && (
              <div className="grid justify-items-start gap-3">
                <p className="text-text-2">Can’t reach the Crucible backend, so this section can’t load.</p>
                {error && <p className="text-text-3" style={{ fontSize: "var(--fs-meta)" }}>{error}</p>}
                <BtnPrimary onClick={() => { setError(null); vscode.postMessage({ type: "settings/load" }); }}>Retry</BtnPrimary>
              </div>
            )}
            {props && section === "overview" && <OverviewSection state={props.state} onNavigate={setSection} />}
            {props && section === "provider" && <ProviderSection {...props} />}
            {props && section === "mcp" && <McpSection {...props} />}
            {props && section === "skills" && <SkillsSection {...props} />}
            {props && section === "agents" && <AgentsSection {...props} />}
            {props && section === "instructions" && (
              <InstructionsSection instructions={instructions} busy={busy} send={send} />
            )}
            {props && section === "policies" && <PoliciesSection {...props} />}
            {props && section === "runtime" && <RuntimeSection {...props} />}
          </div>
```

If `OverviewSection` renders a card per `SECTIONS` entry, its own tests may need `appearance` in their expected lists. Update them to match the new registry.

- [ ] **Step 8: Run the webview tests, typecheck and build**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run && npx tsc --noEmit && npx vite build`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/vscode-extension/src/settings-sections.ts apps/vscode-extension/src/settings-data.ts apps/vscode-extension/src/settings-deps.ts apps/vscode-extension/test/settings-sections.test.ts apps/vscode-extension/test/settings-data.test.ts apps/vscode-extension/webview-ui/src/settings
git commit -m "feat(settings): Appearance section; Settings shell renders without a backend"
```

---

### Task 6: Colour-only cleanup of the other panels

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/setup/StepRail.tsx:51`, `setup/SetupApp.tsx:203`, `memory/MemoryApp.tsx:35`, `settings/SettingsApp.tsx` (the restart banner border), `components/SettingsDrawer.tsx:47`, `components/shared/buttons.tsx:38`, `components/shared/Switch.tsx:42`
- Modify: `apps/vscode-extension/webview-ui/src/test/themeLint.test.ts` (extend `LINT_SCOPE`)

**Interfaces:**
- Consumes: Task 1's tokens `--on-accent`, `--warning-border`, `--accent-bg`, `--shadow-control`, `--highlight-inset`, `--accent-glow`.

- [ ] **Step 1: Extend the lint scope so it fails**

In `src/test/themeLint.test.ts`, set:

```ts
export const LINT_SCOPE = [
  "index.css",
  "theme.ts",
  "setup/StepRail.tsx",
  "setup/SetupApp.tsx",
  "memory/MemoryApp.tsx",
  "components/SettingsDrawer.tsx",
];
```

These files are linted for literal **colours** in this phase. Their font sizes and radii migrate with their layouts later, so in the test, add `const COLOR_ONLY = new Set(["setup/StepRail.tsx", "setup/SetupApp.tsx", "memory/MemoryApp.tsx", "components/SettingsDrawer.tsx"]);`. In the filter, apply only `COLOR_LITERAL` when `COLOR_ONLY.has(file)`:

```ts
        .filter(({ text }) => COLOR_ONLY.has(file)
          ? COLOR_LITERAL.test(text)
          : COLOR_LITERAL.test(text) || TW_SIZE.test(text) || TW_RADIUS.test(text)
            || INLINE_SIZE.test(text) || (file.endsWith(".css") && CSS_SIZE.test(text)))
```

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/themeLint.test.ts`
Expected: FAIL, listing the four `#fff` and `rgba(…)` lines.

- [ ] **Step 2: Replace the literals**

- `setup/StepRail.tsx:51`, `setup/SetupApp.tsx:203`: `color: "#fff",` → `color: "var(--on-accent)",`
- `memory/MemoryApp.tsx:35`: `{ background: "var(--color-accent-deep)", color: "#fff" }` → `{ background: "var(--accent-fill)", color: "var(--on-accent)" }`
- `settings/SettingsApp.tsx` restart banner: `borderColor: "rgba(251,191,36,.3)"` → `borderColor: "var(--warning-border)"`
- `components/SettingsDrawer.tsx:47`: `"linear-gradient(180deg, rgba(139,92,246,.10), rgba(139,92,246,0)), transparent"` → `"linear-gradient(180deg, var(--accent-bg), transparent), transparent"`
- `components/shared/buttons.tsx:38`: the box-shadow string → `"var(--shadow-control), 0 0 16px var(--accent-glow), var(--highlight-inset)"`
- `components/shared/Switch.tsx:42`: `boxShadow: "0 1px 2px rgba(0,0,0,.5)",` → `boxShadow: "var(--shadow-control)",`

`components/shared/*` is linted in Phase 2. This step only removes its colour literals, so high contrast turns the shadows off.

- [ ] **Step 3: Run the lint and the full webview suite**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run && npx vite build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/setup apps/vscode-extension/webview-ui/src/memory/MemoryApp.tsx apps/vscode-extension/webview-ui/src/settings/SettingsApp.tsx apps/vscode-extension/webview-ui/src/components/SettingsDrawer.tsx apps/vscode-extension/webview-ui/src/components/shared/buttons.tsx apps/vscode-extension/webview-ui/src/components/shared/Switch.tsx apps/vscode-extension/webview-ui/src/test/themeLint.test.ts
git commit -m "refactor(webview): colour-only token cleanup in setup, memory and settings panels"
```

---

### Task 7: Real-browser verification and audit

**Files:**
- Create: `scripts/dev/cdp-theme-check.mjs`

**Interfaces:**
- Consumes: the running VS Code main instance with `--remote-debugging-port=9335`, the extension installed through `scripts/dev/install-local.sh`, and the attributes and tokens from Tasks 1–6.

- [ ] **Step 1: Write the CDP check script**

Create `scripts/dev/cdp-theme-check.mjs`:

```js
#!/usr/bin/env node
// Real-browser theme check (spec 2026-10-13 Testing → computed-colour checks). jsdom
// can't resolve CSS variables, so this reads computed styles from the live Crucible
// webviews over CDP. Usage: node scripts/dev/cdp-theme-check.mjs [expectedTheme] [expectedAccent]
const [expectedTheme, expectedAccent] = process.argv.slice(2);
const PORT = Number(process.env.CDP_PORT ?? 9335);

const targets = await (await fetch(`http://localhost:${PORT}/json/list`)).json();
const webviews = targets.filter((t) => t.type === "iframe" && t.url.startsWith("vscode-webview://"));
if (webviews.length === 0) throw new Error("No webview targets — open a Crucible panel first.");

// Evaluated inside each webview target. VS Code nests the page document in a child
// iframe; pick the document that has #root.
const PROBE = `(() => {
  const docs = [document, ...Array.from(document.querySelectorAll('iframe')).map((f) => { try { return f.contentDocument; } catch { return null; } })];
  const doc = docs.find((d) => d && d.getElementById('root'));
  if (!doc) return null;
  const root = doc.documentElement, cs = getComputedStyle(root), body = getComputedStyle(doc.body);
  const v = (n) => cs.getPropertyValue(n).trim();
  return { title: doc.title, theme: root.dataset.theme, tone: root.dataset.tone, accent: root.dataset.accent,
    bodyClass: doc.body.className, bodyBg: body.backgroundColor, bodyFg: body.color,
    tokens: { surface0: v('--surface-0'), text1: v('--text-1'), accent: v('--accent'), accentFill: v('--accent-fill'), border: v('--border') } };
})()`;

let failures = 0;
for (const target of webviews) {
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  const result = await new Promise((resolve) => {
    ws.addEventListener("message", (e) => {
      const m = JSON.parse(e.data);
      if (m.id === 1) resolve(m.result?.result?.value ?? null);
    });
    ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression: PROBE, returnByValue: true } }));
  });
  ws.close();
  if (!result) continue;
  const problems = [];
  if (expectedTheme && result.theme !== expectedTheme) problems.push(`theme ${result.theme} ≠ ${expectedTheme}`);
  if (expectedAccent && result.theme !== "hc" && result.accent !== expectedAccent) problems.push(`accent ${result.accent} ≠ ${expectedAccent}`);
  if (Object.values(result.tokens).some((t) => t === "")) problems.push("an expected token resolved to empty");
  if (result.bodyBg === "rgba(0, 0, 0, 0)") problems.push("body background is transparent");
  failures += problems.length;
  console.log(`${problems.length ? "✗" : "✓"} ${result.title}  theme=${result.theme} tone=${result.tone} accent=${result.accent}  bg=${result.bodyBg} fg=${result.bodyFg}`, problems.join("; "));
}
process.exit(failures ? 1 : 0);
```

- [ ] **Step 2: Build, install and restart VS Code**

Run:

```bash
npm run -w crucible-vscode-extension build
bash scripts/dev/install-local.sh
osascript -e 'quit app "Visual Studio Code"'
open -a "Visual Studio Code" --args --remote-debugging-port=9335 "$PWD"
```

Then open the Crucible chat, Settings, Setup and Memory panels.

- [ ] **Step 3: Check each combination**

Run `node scripts/dev/cdp-theme-check.mjs` and capture screenshots (with `Page.captureScreenshot` on the window target, as in the smoke recipe) for each combination below. Change `crucible.appearance` and `crucible.accent` from Settings › Appearance. Change the editor theme with **Preferences: Color Theme**, driven over CDP, or ask the user to switch it. Never edit the user's global `settings.json` to do it.

| Editor theme | `crucible.appearance` | Expected `data-theme` |
|---|---|---|
| Dark Modern | vscode | `vscode` (tone dark) |
| Light Modern | vscode | `vscode` (tone light) |
| Dark Modern | light | `light` |
| Light Modern | dark | `dark` |
| Dark High Contrast | light | `hc` (tone dark) |
| Light High Contrast | dark | `hc` (tone light) |

For each row, run `node scripts/dev/cdp-theme-check.mjs <expectedTheme> blue`, then switch the accent to violet and to ember, and re-run it with that accent. Expected: every line prints `✓`, and the exit code is 0. Also:

- Reload the chat webview (**Developer: Reload Webviews**) and confirm the restored panel keeps its theme.
- Stop the backend and open Settings: the nav and Appearance must still work.

- [ ] **Step 4: Audit with the installed skill**

Invoke the `web-design-guidelines` skill on:
- `apps/vscode-extension/webview-ui/src/styles/themes.css`
- `src/index.css`
- `src/theme.ts`
- `src/settings/SettingsApp.tsx`
- `src/settings/sections/AppearanceSection.tsx`

Fix each finding, or record why it's kept, in the commit message of the fix.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/cdp-theme-check.mjs
git commit -m "test(dev): CDP computed-colour theme check"
```

---

## Self-review notes

- **Spec coverage (§1 and the Phase 1 rows):**
  - tokens, scales and the four theme sets — Task 1;
  - paired surface foregrounds — Task 1. The contextual `.on-surface-*` classes are introduced in Phase 2, with the first components that sit on cards (no Phase 1 component needs them);
  - `theme.ts`, high contrast and live body classes — Task 3;
  - settings, injection and `themeChanged` — Task 4;
  - Appearance, offline state, both registries and both mirrors — Task 5. The chat overlay embeds `SettingsApp` and routes `settings/*` through the same handler (`chat-panel.ts:259`), so it gets the section and the offline shell with no extra wiring;
  - colour-only panel cleanup — Task 6;
  - contrast, lint and real-browser checks — Tasks 1, 2 and 7.
- **Deferred to Phase 2 and 3 plans:** the transcript and composer migrations, the approval mode, and the chat-component lint scope.
- **Names used consistently:** `installThemeSync`, `resolveTheme`, `applyTheme`, `themeHub`, `ThemeSettings`, `readThemeConfig`, `settings/localState`, `settings/setAppearance`, `LINT_SCOPE`.
