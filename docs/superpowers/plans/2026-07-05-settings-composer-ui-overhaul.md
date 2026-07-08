# Settings, Setup Wizard & Composer UI Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Settings panel as an animated Copilot-style left-nav surface, reskin the Setup wizard onto the chat design system, add an AGENTS.md editor section, and add a live model-switch + settings shortcut to the chat composer.

**Architecture:** Pure presentation-layer restructure of the two secondary webview entries (`settings.html`, `setup.html`) plus a small composer addition in the chat entry. Data flow is unchanged (`settings/load` full-snapshot rebuild); two new host seams are added: AGENTS.md read/write (direct `fs`, `prompt-files.ts` pattern) and a composer model hot-swap path (SecretStorage-keyed providers → existing `PUT /v1/config/provider` route).

**Tech Stack:** React 18 + Tailwind v4 (`@theme` tokens in `index.css`), CSS-only animations (no new npm deps — the webview CSP forbids external assets anyway), vitest + @testing-library/react (webview), vitest node-env (extension host).

**Spec:** `docs/superpowers/specs/2026-07-04-settings-composer-ui-overhaul-design.md`
**Visual reference:** `agents_ui/Screenshot 2026-07-04 at 11.14.05 PM.png` (Overview card grid), `…11.14.41 PM.png` (section layout + empty state), `…11.14.42 PM.png` (list rows), `…11.14.47 PM.png` (toolbar actions). The low-fi brainstorm wireframes in `.superpowers/brainstorm/86077-1783187706/` chose the *structure* (left-nav, option B; composer option A + gear) — **§0 of this plan supersedes them as the visual source of truth.**

**Recorded deviations from the spec (all in the "cooler UI" direction the user asked for):**
1. Setup install-progress glyphs upgrade from text emoji (⏳/✓/✗/⤷) to animated SVG icons with the same four-state mapping (spec §5 said "preserved as-is"; the status semantics ARE preserved).
2. Skills and MCP sections gain a client-side search filter (matches the Copilot screenshots; presentation-only, no data-flow change).
3. Composer model dropdown includes a local provider (ollama/turboquant) only when it is the *currently active* backend (spec's keyed-only rule would otherwise hide the active provider from its own dropdown).

## Global Constraints

- **No new npm dependencies** — animations are CSS keyframes/transitions only; icons are inline SVG in `Icon.tsx`.
- **All colors come from the existing `index.css` tokens** (`--color-accent`, `--color-surface-*`, etc.). Never hardcode a hex in a component.
- **Every animation must respect `prefers-reduced-motion`** (global kill switch added in Task 1).
- **Webview bundles never import the extension's `src/`** — mirror types locally (existing convention).
- **Build order:** after `editor-client` changes run `npm run -w @crucible/editor-client build` first (not needed here — no contract changes), and **always rebuild `webview-ui` (`npm run -w crucible-vscode-extension build`) before a dev-host smoke** — stale `dist/` is a known footgun.
- **Workspace-scoped test commands:** webview tests run from `apps/vscode-extension/webview-ui` (`npx vitest run <file>`), extension host tests via `npm run -w crucible-vscode-extension test -- <file>`.
- Commit format: `type(scope): description`.

---

## §0 Design Direction (the source of truth for look & motion)

### 0.1 Layout — Settings

```
┌────────────────────────────────────────────────────────────┐
│ ┌──────────────┐ ┌───────────────────────────────────────┐ │
│ │ ⌂ Overview   │ │  [restart-required banner, slides in] │ │
│ │ 🔑 Provider  │ │                                       │ │
│ │ 🔌 MCP    2  │ │  Section title        (28px, semibold)│ │
│ │ ⚡ Skills 14 │ │  One-line description  (text-3)       │ │
│ │ 📖 Instruct. │ │                                       │ │
│ │ 🛡 Policies  │ │  [search field]        [action button] │ │
│ │ ▣ Runtime    │ │                                       │ │
│ │              │ │  CardShell content …                  │ │
│ └──────────────┘ └───────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

- **Nav rail:** 168px fixed, right hairline border, one 30px item per section: icon (12px) + label + right-aligned count badge (mono, 9.5px, `surface-3` chip). Active item = `--accent-bg` tint + `--color-accent-ink` text + a 2px glowing accent indicator bar on the left edge that **slides** between items (`transition: top` with the spring ease) instead of teleporting.
- **Content pane:** max-width 560px, centered, 20px padding, scrolls independently. Each section swap re-mounts the pane with `anim-section` (fade + 10px rise, 260ms ease-out) — `key={section}` forces the remount.
- **Overview** is the landing page: a 2-column card grid, one card per section. Cards enter with a **45ms stagger** (`animation-delay: i*45ms`). Each card: 28px rounded icon tile (`--accent-bg` fill, accent icon), bold title + count, two-line blurb in `text-3`. Hover = `hover-lift`: rise 2px, accent border, soft violet glow. Click navigates.
- **Restart-required banner** stays global (visible in every section), amber tint, slides down on appearance (`anim-slide-down`), contains the Restart button.

### 0.2 Layout — Setup wizard

Same 4-step flow. Adds a **StepRail** across the top: four numbered dots joined by connector lines; completed dots turn green with a pop-in check, the active dot breathes with the existing `breathe` glow keyframe, connector lines fill with accent as you advance. Each step body is a `CardShell`. The welcome step gets a hero: a 44px gradient tile with the `spark` icon breathing, headline + sub, and the component list as staggered rows. Install rows: spinner while running, green check **pops in** (`anim-pop`, spring scale) on done, red ✗ + detail line on failure, amber ⤷ on skipped.

### 0.3 Layout — Composer quick-bar

Footer row gains, at the far left: **[✦ model-name ▾] [⚙]**.
- The model chip is a ghost pill (surface-2, hairline border, 10.5px mono-ish model name truncated to 18 chars). Click opens a **popover above the composer** (`bottom-full`, `anim-rise`): one row per available provider — provider label (9.5px, text-3, uppercase) over model name (11px), active row marked with an accent check. While a swap is in flight the chosen row shows a mini spinner; on failure a red error line renders inside the popover (popover stays open). Footer row of the popover: "Provider settings…" ghost action → opens the Settings panel.
- The gear is a 24px icon button (text-3 → text on hover, surface-2 hover tint) that runs `crucible.openSettingsPanel`.

### 0.4 Motion system (added to `index.css`, used everywhere)

| Token | Value | Use |
|---|---|---|
| `--dur-fast` | 120ms | hovers, color shifts |
| `--dur-base` | 180ms | indicator slide, switch knob, chevrons |
| `--dur-slow` | 260ms | section entrances, card stagger |
| `--ease-out` | `cubic-bezier(.2,.8,.3,1)` | all entrances |
| `--ease-spring` | `cubic-bezier(.34,1.56,.64,1)` | knobs, indicator, pop-ins (subtle overshoot) |

Keyframes added: `section-in` (fade + rise), `pop-in` (spring scale from .4), `slide-down` (banner), `dot-pulse` (connecting-state MCP dot ring). Existing `spin`/`shimmer`/`breathe`/`rise` are reused. A `prefers-reduced-motion: reduce` block zeroes every animation/transition globally.

### 0.5 Micro-interactions inventory

- **Switch** (new shared component, replaces every raw checkbox in Settings): 26×15px track, gradient accent fill + glow when on, white knob slides with spring ease.
- **MCP status dot:** colored 7px dot (green/amber/red/gray); `connecting` pulses an expanding ring.
- **Save feedback:** after a successful provider save or AGENTS.md save, a green "✓ Saved" chip pops in next to the button and fades after ~2s.
- **Buttons:** `BtnPrimary`/`BtnGhost`/`BtnDanger` from the chat design system everywhere — zero raw `<button className="bg-blue-600">` remain.
- **Form fields:** one shared `FIELD` class — surface-2 fill, border-strong, accent border on focus (transition), `placeholder:text-text-4`.

---

## File Structure

**Created (webview `apps/vscode-extension/webview-ui/src/`):**
- `settings/sections/meta.ts` — section registry (`SectionId`, `SECTIONS`, `SectionProps`) shared by NavRail/Overview/shell
- `settings/ui.ts` — `FIELD` shared input class
- `settings/NavRail.tsx`, `settings/SectionHeader.tsx`
- `settings/sections/OverviewSection.tsx`, `ProviderSection.tsx`, `McpSection.tsx`, `SkillsSection.tsx`, `InstructionsSection.tsx`, `PoliciesSection.tsx`, `RuntimeSection.tsx`
- `settings/mcpEntry.ts` — `splitCommandLine`/`buildMcpEntry` moved out of `SettingsApp.tsx`
- `components/shared/Switch.tsx`
- `components/ModelMenu.tsx` (chat entry)
- `setup/StepRail.tsx`
- test files alongside (listed per task)

**Created (extension host `apps/vscode-extension/src/`):**
- `instructions-file.ts` — vscode-free AGENTS.md read/write
- `composer-models.ts` — vscode-free model-option assembly

**Modified:**
- `webview-ui/src/index.css` (motion tokens/keyframes), `components/Icon.tsx` (7 new icons)
- `webview-ui/src/settings/SettingsApp.tsx` (becomes thin shell), `settings/types.ts` (instructions messages)
- `webview-ui/src/setup/SetupApp.tsx`
- `webview-ui/src/components/InputArea.tsx`, `webview-ui/src/types.ts` (ModelOption mirror)
- `src/settings-data.ts` (+2 deps, +2 message cases), `src/settings-panel.ts` (wire fs deps)
- `src/runtime/vscode-runtime.ts` (`getProviderKey`), `src/controller.ts` (`configClient`)
- `src/chat-panel.ts` (+3 callbacks/messages), `src/extension.ts` (wiring)

---

### Task 1: Motion tokens, keyframes, reduced-motion + new icons

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/index.css`
- Modify: `apps/vscode-extension/webview-ui/src/components/Icon.tsx`
- Test: `apps/vscode-extension/webview-ui/src/test/icons.test.tsx` (new)

**Interfaces:**
- Produces: CSS utility classes `anim-section`, `anim-pop`, `anim-slide-down`, `hover-lift`; CSS vars `--dur-fast|base|slow`, `--ease-out`, `--ease-spring`; keyframes `section-in`, `pop-in`, `slide-down`, `dot-pulse`. IconName gains `"home" | "key" | "plug" | "book" | "shield" | "chip" | "gear"`. Every later task consumes these — names must match exactly.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/test/icons.test.tsx
import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Icon } from "../components/Icon";
import type { IconName } from "../components/Icon";

const NEW_ICONS: IconName[] = ["home", "key", "plug", "book", "shield", "chip", "gear"];

describe("Icon — settings/composer additions", () => {
  it.each(NEW_ICONS)("renders %s as a non-empty svg", (name) => {
    const { container } = render(<Icon name={name} size={14} />);
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg!.innerHTML.length).toBeGreaterThan(10);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/icons.test.tsx`
Expected: FAIL — TS2322 / undefined icon entries (the names aren't in `IconName`).

- [ ] **Step 3: Add the icons**

In `Icon.tsx`, extend the union:

```ts
export type IconName =
  | "spark" | "search" | "plus" | "clock" | "chev-r" | "chev-l" | "chev-d"
  | "check" | "x" | "copy" | "file" | "term" | "list" | "diff" | "warn"
  | "send" | "stop" | "retry" | "bolt" | "bug"
  | "home" | "key" | "plug" | "book" | "shield" | "chip" | "gear";
```

Add to `ICONS` (16×16 viewBox, stroke style matching the existing set):

```tsx
  home: (
    <>
      <path d="M2.5 7.5L8 2.5l5.5 5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 7v6.5h8V7" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),

  key: (
    <>
      <circle cx="5" cy="8" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M7.6 8h6M11 8v2.4M13.6 8v1.8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </>
  ),

  plug: (
    <>
      <path d="M5.5 2v3M10.5 2v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M4 5h8v2.5a4 4 0 01-8 0V5z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M8 11.5V14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </>
  ),

  book: (
    <>
      <path d="M3 3a1.5 1.5 0 011.5-1.5H13v11H4.8A1.8 1.8 0 003 14.3V3z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M13 12.5H4.8A1.8 1.8 0 003 14.3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M6 5h4.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),

  shield: (
    <>
      <path d="M8 1.8l5.3 1.9v4.4c0 3.2-2.2 5.4-5.3 6.4-3.1-1-5.3-3.2-5.3-6.4V3.7L8 1.8z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M5.7 8l1.7 1.7L10.6 6.4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),

  chip: (
    <>
      <rect x="4.5" y="4.5" width="7" height="7" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M6.5 4.5V2M9.5 4.5V2M6.5 14v-2.5M9.5 14v-2.5M4.5 6.5H2M4.5 9.5H2M14 6.5h-2.5M14 9.5h-2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),

  gear: (
    <>
      <circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M12.4 3.6L11 5M5 11l-1.4 1.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </>
  ),
```

- [ ] **Step 4: Add the motion system to `index.css`**

Append to the `:root` block:

```css
  /* motion tokens */
  --dur-fast: 120ms;
  --dur-base: 180ms;
  --dur-slow: 260ms;
  --ease-out: cubic-bezier(.2,.8,.3,1);
  --ease-spring: cubic-bezier(.34,1.56,.64,1);
```

Append after the existing keyframes/utilities:

```css
@keyframes section-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: none; }
}

@keyframes pop-in {
  0% { opacity: 0; transform: scale(.4); }
  70% { transform: scale(1.12); }
  100% { opacity: 1; transform: scale(1); }
}

@keyframes slide-down {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: none; }
}

@keyframes dot-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(251,191,36,.45); }
  50% { box-shadow: 0 0 0 4px rgba(251,191,36,0); }
}

.anim-section { animation: section-in var(--dur-slow) var(--ease-out) both; }
.anim-pop { animation: pop-in 240ms var(--ease-spring) both; }
.anim-slide-down { animation: slide-down var(--dur-base) var(--ease-out) both; }

/* Overview-card / list hover treatment */
.hover-lift {
  transition:
    transform var(--dur-base) var(--ease-out),
    border-color var(--dur-base) var(--ease-out),
    box-shadow var(--dur-base) var(--ease-out);
}
.hover-lift:hover {
  transform: translateY(-2px);
  border-color: var(--accent-brd) !important;
  box-shadow: 0 6px 20px -8px rgba(0,0,0,.6), 0 0 20px var(--accent-glow);
}

/* Accessibility: kill all motion when the user asks for it */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
  }
}
```

- [ ] **Step 5: Run tests + typecheck**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/icons.test.tsx && npx tsc --noEmit`
Expected: PASS, clean typecheck.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/index.css apps/vscode-extension/webview-ui/src/components/Icon.tsx apps/vscode-extension/webview-ui/src/test/icons.test.tsx
git commit -m "feat(webview): motion token system, reduced-motion guard, and 7 settings/composer icons"
```

---

### Task 2: `Switch` shared component

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/shared/Switch.tsx`
- Test: `apps/vscode-extension/webview-ui/src/test/Switch.test.tsx`

**Interfaces:**
- Produces: `Switch({ checked: boolean; onChange: (next: boolean) => void; disabled?: boolean; label?: string })` — `role="switch"`, `aria-checked`. Consumed by McpSection (Task 7) and SkillsSection (Task 8).

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/test/Switch.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { Switch } from "../components/shared/Switch";

describe("Switch", () => {
  it("reflects checked state via aria and toggles on click", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} label="Enable web" />);
    const sw = screen.getByRole("switch", { name: "Enable web" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("does not fire when disabled", () => {
    const onChange = vi.fn();
    render(<Switch checked disabled onChange={onChange} label="x" />);
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/Switch.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// apps/vscode-extension/webview-ui/src/components/shared/Switch.tsx
interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: string;
}

/**
 * Switch — animated toggle replacing raw checkboxes in Settings.
 * Track fills with the accent gradient + glow when on; the knob slides
 * with the spring ease. Motion collapses under prefers-reduced-motion.
 */
export function Switch({ checked, onChange, disabled, label }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative inline-flex w-[26px] h-[15px] flex-shrink-0 rounded-full border cursor-pointer disabled:opacity-50 disabled:cursor-default"
      style={{
        background: checked
          ? "linear-gradient(180deg, var(--color-accent-deep), var(--color-accent-hot))"
          : "var(--color-surface-3)",
        borderColor: checked ? "transparent" : "var(--color-border-strong)",
        boxShadow: checked ? "0 0 10px var(--accent-glow)" : "none",
        transition: "background var(--dur-base) var(--ease-out), box-shadow var(--dur-base) var(--ease-out)",
      }}
    >
      <span
        aria-hidden="true"
        className="absolute rounded-full bg-white"
        style={{
          width: 11,
          height: 11,
          top: 1,
          left: checked ? 12 : 1,
          transition: "left var(--dur-base) var(--ease-spring)",
          boxShadow: "0 1px 2px rgba(0,0,0,.5)",
        }}
      />
    </button>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/test/Switch.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/shared/Switch.tsx apps/vscode-extension/webview-ui/src/test/Switch.test.tsx
git commit -m "feat(webview): shared animated Switch component"
```

---

### Task 3: Section registry + NavRail

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/meta.ts`
- Create: `apps/vscode-extension/webview-ui/src/settings/ui.ts`
- Create: `apps/vscode-extension/webview-ui/src/settings/NavRail.tsx`
- Test: `apps/vscode-extension/webview-ui/src/settings/NavRail.test.tsx`

**Interfaces:**
- Produces:
  - `type SectionId = "overview" | "provider" | "mcp" | "skills" | "instructions" | "policies" | "runtime"`
  - `SECTIONS: SectionMeta[]` (`{ id, label, icon, blurb }`, excludes overview)
  - `interface SectionProps { state: SettingsState; busy: boolean; send: (msg: SettingsInMsg) => void }`
  - `NavRail({ active, counts, onSelect })` where `counts: Partial<Record<SectionId, number>>`
  - `FIELD: string` — the shared input class

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/settings/NavRail.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { NavRail } from "./NavRail";

describe("NavRail", () => {
  it("lists all seven sections and fires onSelect", () => {
    const onSelect = vi.fn();
    render(<NavRail active="overview" counts={{ skills: 14, mcp: 2 }} onSelect={onSelect} />);
    for (const label of ["Overview", "Provider", "MCP Servers", "Skills", "Instructions", "Policies & Memory", "Runtime"]) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: /Skills/ }));
    expect(onSelect).toHaveBeenCalledWith("skills");
  });

  it("shows count badges and marks the active item", () => {
    render(<NavRail active="skills" counts={{ skills: 14 }} onSelect={() => {}} />);
    expect(screen.getByText("14")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Skills/ }).getAttribute("aria-current")).toBe("page");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/NavRail.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the registry, FIELD, and NavRail**

```ts
// apps/vscode-extension/webview-ui/src/settings/sections/meta.ts
import type { IconName } from "../../components/Icon";
import type { SettingsInMsg, SettingsState } from "../types";

export type SectionId =
  | "overview" | "provider" | "mcp" | "skills" | "instructions" | "policies" | "runtime";

export interface SectionMeta {
  id: Exclude<SectionId, "overview">;
  label: string;
  icon: IconName;
  blurb: string;
}

/** Nav + Overview registry. Order = nav order = Overview grid order. */
export const SECTIONS: SectionMeta[] = [
  { id: "provider", label: "Provider", icon: "key", blurb: "Model provider, API key, and instant model hot-swap." },
  { id: "mcp", label: "MCP Servers", icon: "plug", blurb: "External tool servers that extend the agent with new tools." },
  { id: "skills", label: "Skills", icon: "bolt", blurb: "Workspace skill catalog the agent can load when relevant." },
  { id: "instructions", label: "Instructions", icon: "book", blurb: "Project instructions (AGENTS.md) injected into every turn." },
  { id: "policies", label: "Policies & Memory", icon: "shield", blurb: "Shell & scope approval policies, memory harness flags." },
  { id: "runtime", label: "Runtime", icon: "chip", blurb: "Installed runtime components, versions, and backend restart." },
];

/** Common props every section component receives from the SettingsApp shell. */
export interface SectionProps {
  state: SettingsState;
  busy: boolean;
  send: (msg: SettingsInMsg) => void;
}
```

```ts
// apps/vscode-extension/webview-ui/src/settings/ui.ts
/** Shared form-field class — surface-2 fill, accent focus border. */
export const FIELD =
  "rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-xs text-text " +
  "outline-none transition-colors duration-150 focus:border-[var(--color-accent)] " +
  "placeholder:text-text-4";
```

```tsx
// apps/vscode-extension/webview-ui/src/settings/NavRail.tsx
import { Icon } from "../components/Icon";
import type { IconName } from "../components/Icon";
import { SECTIONS, type SectionId } from "./sections/meta";

interface NavRailProps {
  active: SectionId;
  counts: Partial<Record<SectionId, number>>;
  onSelect: (id: SectionId) => void;
}

const ITEM_H = 30;
const ITEM_GAP = 2;
const PAD_TOP = 8; // p-2

/**
 * NavRail — Copilot-style left navigation. A single glowing 2px indicator
 * bar slides (spring ease) to the active item instead of re-rendering per row.
 */
export function NavRail({ active, counts, onSelect }: NavRailProps) {
  const items: { id: SectionId; label: string; icon: IconName }[] = [
    { id: "overview", label: "Overview", icon: "home" },
    ...SECTIONS.map((s) => ({ id: s.id as SectionId, label: s.label, icon: s.icon })),
  ];
  const activeIdx = Math.max(0, items.findIndex((i) => i.id === active));

  return (
    <nav
      aria-label="Settings sections"
      className="relative flex w-[168px] flex-shrink-0 flex-col p-2"
      style={{ borderRight: "1px solid var(--color-border)" }}
    >
      {/* Sliding active indicator */}
      <span
        aria-hidden="true"
        className="absolute w-[2px] rounded-full"
        style={{
          left: 3,
          top: PAD_TOP + activeIdx * (ITEM_H + ITEM_GAP) + 7,
          height: 16,
          background: "var(--color-accent)",
          boxShadow: "0 0 8px var(--accent-glow)",
          transition: "top var(--dur-base) var(--ease-spring)",
        }}
      />
      {items.map((item) => {
        const isActive = item.id === active;
        return (
          <button
            key={item.id}
            type="button"
            aria-current={isActive ? "page" : undefined}
            onClick={() => onSelect(item.id)}
            className={[
              "flex items-center gap-2 h-[30px] mb-[2px] px-2.5 rounded-md",
              "text-xs text-left cursor-pointer border-0 bg-transparent",
              "transition-colors duration-150",
              isActive ? "" : "text-text-2 hover:bg-surface-2 hover:text-text",
            ].join(" ")}
            style={isActive ? { background: "var(--accent-bg)", color: "var(--color-accent-ink)" } : undefined}
          >
            <Icon name={item.icon} size={12} />
            <span className="flex-1 truncate">{item.label}</span>
            {counts[item.id] !== undefined && (
              <span
                className="rounded px-1 font-mono"
                style={{ fontSize: "9.5px", background: "var(--color-surface-3)", color: "var(--color-text-3)" }}
              >
                {counts[item.id]}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/NavRail.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings/sections/meta.ts apps/vscode-extension/webview-ui/src/settings/ui.ts apps/vscode-extension/webview-ui/src/settings/NavRail.tsx apps/vscode-extension/webview-ui/src/settings/NavRail.test.tsx
git commit -m "feat(webview): settings section registry and sliding-indicator NavRail"
```

---

### Task 4: SectionHeader

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/SectionHeader.tsx`
- Test: `apps/vscode-extension/webview-ui/src/settings/SectionHeader.test.tsx`

**Interfaces:**
- Produces: `SectionHeader({ title, description, search?, action? })` where `search = { value: string; onChange: (v: string) => void; placeholder?: string }` and `action` is a ReactNode rendered on the search row's right.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/settings/SectionHeader.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SectionHeader } from "./SectionHeader";

describe("SectionHeader", () => {
  it("renders title and description", () => {
    render(<SectionHeader title="Skills" description="Workspace skill catalog." />);
    expect(screen.getByRole("heading", { name: "Skills" })).toBeTruthy();
    expect(screen.getByText("Workspace skill catalog.")).toBeTruthy();
  });

  it("wires the search input when provided", () => {
    const onChange = vi.fn();
    render(
      <SectionHeader
        title="Skills"
        description="d"
        search={{ value: "", onChange, placeholder: "Type to search…" }}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("Type to search…"), { target: { value: "web" } });
    expect(onChange).toHaveBeenCalledWith("web");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/SectionHeader.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// apps/vscode-extension/webview-ui/src/settings/SectionHeader.tsx
import type { ReactNode } from "react";
import { Icon } from "../components/Icon";
import { FIELD } from "./ui";

interface SearchProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}

interface Props {
  title: string;
  description: string;
  search?: SearchProps;
  /** Right-aligned affordance on the search row (e.g. an add button). */
  action?: ReactNode;
}

/** Copilot-style section header: big title, one-line description, optional search+action row. */
export function SectionHeader({ title, description, search, action }: Props) {
  return (
    <header className="mb-4 flex flex-col gap-1">
      <h1 className="text-base font-semibold text-text">{title}</h1>
      <p className="text-xs leading-relaxed text-text-3">{description}</p>
      {(search || action) && (
        <div className="mt-2 flex items-center gap-2">
          {search && (
            <div className="relative flex-1">
              <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-4">
                <Icon name="search" size={11} />
              </span>
              <input
                className={`${FIELD} w-full pl-7`}
                value={search.value}
                placeholder={search.placeholder ?? "Type to search…"}
                onChange={(e) => search.onChange(e.target.value)}
              />
            </div>
          )}
          {action}
        </div>
      )}
    </header>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/SectionHeader.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings/SectionHeader.tsx apps/vscode-extension/webview-ui/src/settings/SectionHeader.test.tsx
git commit -m "feat(webview): settings SectionHeader with optional search row"
```

---

### Task 5: OverviewSection

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/OverviewSection.tsx`
- Test: `apps/vscode-extension/webview-ui/src/settings/sections/OverviewSection.test.tsx`

**Interfaces:**
- Consumes: `SECTIONS` from `meta.ts`, `SectionHeader`, `Icon`, `hover-lift`/`anim-section` CSS.
- Produces: `OverviewSection({ state, onNavigate }: { state: SettingsState; onNavigate: (id: SectionId) => void })`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/OverviewSection.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { OverviewSection } from "./OverviewSection";
import type { SettingsState } from "../types";

const state: SettingsState = {
  provider: { backend: "gemini", model: "gemini-flash-latest" },
  runtime: null,
  mcp: { enabled: true, servers: [{ name: "web", transport: "stdio", enabledInFile: true, state: "connected", detail: null, toolCount: 2, userEnabled: true }] },
  skills: [{ name: "code-review", description: "d", enabled: true }],
  envFlags: {},
  restartRequired: false,
};

describe("OverviewSection", () => {
  it("renders a card per section with counts, navigating on click", () => {
    const onNavigate = vi.fn();
    render(<OverviewSection state={state} onNavigate={onNavigate} />);
    expect(screen.getByText("MCP Servers")).toBeTruthy();
    expect(screen.getByText("1 server")).toBeTruthy();
    expect(screen.getByText("1 skill")).toBeTruthy();
    // Provider card shows the live backend/model
    expect(screen.getByText(/gemini-flash-latest/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Instructions/ }));
    expect(onNavigate).toHaveBeenCalledWith("instructions");
  });
});
```

Note the import path: the test lives in `sections/`, so `SettingsState` comes from `../types`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/sections/OverviewSection.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/OverviewSection.tsx
import { Icon } from "../../components/Icon";
import { SectionHeader } from "../SectionHeader";
import type { SettingsState } from "../types";
import { SECTIONS, type SectionId } from "./meta";

interface Props {
  state: SettingsState;
  onNavigate: (id: SectionId) => void;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/**
 * OverviewSection — the landing card grid (one card per section), mirroring
 * Copilot CLI's Overview page. Cards stagger in and lift on hover; the
 * subline of each card surfaces the live state (active model, counts).
 */
export function OverviewSection({ state, onNavigate }: Props) {
  const sublines: Partial<Record<SectionId, string>> = {
    provider: state.provider ? `${state.provider.backend} · ${state.provider.model}` : "not configured",
    mcp: plural(state.mcp.servers.length, "server"),
    skills: plural(state.skills.length, "skill"),
    runtime: state.runtime ? `release ${state.runtime.releaseTag}` : "not installed",
  };

  return (
    <div>
      <SectionHeader
        title="Settings"
        description="Configure the provider, tools, and policies that shape how the agent works in this workspace."
      />
      <div className="grid grid-cols-2 gap-3">
        {SECTIONS.map((s, i) => (
          <button
            key={s.id}
            type="button"
            onClick={() => onNavigate(s.id)}
            className="hover-lift anim-section flex cursor-pointer flex-col items-start gap-2 rounded-[10px] border bg-surface p-3.5 text-left"
            style={{ borderColor: "var(--color-border)", animationDelay: `${i * 45}ms` }}
          >
            <span
              className="flex h-7 w-7 items-center justify-center rounded-[8px]"
              style={{ background: "var(--accent-bg)", color: "var(--color-accent)" }}
            >
              <Icon name={s.icon} size={14} />
            </span>
            <span className="text-xs font-semibold text-text">{s.label}</span>
            <span className="text-[11px] leading-relaxed text-text-3">{s.blurb}</span>
            {sublines[s.id] && (
              <span className="font-mono text-[9.5px]" style={{ color: "var(--color-text-4)" }}>
                {sublines[s.id]}
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/sections/OverviewSection.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings/sections/OverviewSection.tsx apps/vscode-extension/webview-ui/src/settings/sections/OverviewSection.test.tsx
git commit -m "feat(webview): settings Overview card grid with staggered entrance"
```

---

### Task 6: ProviderSection

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.tsx`
- Test: `apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.test.tsx`

**Interfaces:**
- Consumes: `SectionProps` (meta.ts), `PROVIDERS` (settings/types.ts), `CardShell`, `BtnPrimary`, `FIELD`.
- Produces: `ProviderSection(props: SectionProps)` — posts `settings/setProvider` exactly as the old flat page did.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ProviderSection } from "./ProviderSection";
import type { SettingsState } from "../types";

const state: SettingsState = {
  provider: { backend: "gemini", model: "gemini-flash-latest" },
  runtime: null,
  mcp: { enabled: false, servers: [] },
  skills: [],
  envFlags: {},
  restartRequired: false,
};

describe("ProviderSection", () => {
  it("prefills from state and posts settings/setProvider on save", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    // Prefilled from state.provider
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("gemini-flash-latest");
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gemini-3-pro" } });
    fireEvent.click(screen.getByRole("button", { name: /Save & validate/ }));
    expect(send).toHaveBeenCalledWith({
      type: "settings/setProvider",
      backend: "gemini",
      model: "gemini-3-pro",
    });
  });

  it("includes apiKey only when typed for a non-local provider", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.change(screen.getByLabelText(/API key/), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByRole("button", { name: /Save & validate/ }));
    expect(send.mock.calls[0][0]).toMatchObject({ apiKey: "sk-test" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/sections/ProviderSection.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnPrimary } from "../../components/shared/buttons";
import { Icon } from "../../components/Icon";
import { SectionHeader } from "../SectionHeader";
import { PROVIDERS } from "../types";
import { FIELD } from "../ui";
import type { SectionProps } from "./meta";

/**
 * ProviderSection — backend/model select + API key + "Save & validate".
 * Behavior is identical to the old flat page; the "✓ Saved" chip pops in
 * when a save round-trip lands a new provider snapshot.
 */
export function ProviderSection({ state, busy, send }: SectionProps) {
  const [backend, setBackend] = useState(state.provider?.backend ?? PROVIDERS[0].id);
  const [model, setModel] = useState(state.provider?.model ?? PROVIDERS[0].defaultModel);
  const [apiKey, setApiKey] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);

  const provider = useMemo(
    () => PROVIDERS.find((p) => p.id === backend) ?? PROVIDERS[0],
    [backend],
  );

  // Flash "✓ Saved" when the active provider snapshot changes after our save.
  const providerSig = state.provider ? `${state.provider.backend}/${state.provider.model}` : "";
  const pendingSave = useRef(false);
  useEffect(() => {
    if (!pendingSave.current) return;
    pendingSave.current = false;
    setSavedFlash(true);
    const id = setTimeout(() => setSavedFlash(false), 2000);
    return () => clearTimeout(id);
  }, [providerSig]);

  return (
    <div>
      <SectionHeader
        title="Provider"
        description="Pick the model provider and model. Saving validates the credentials and hot-swaps the running backend — no restart."
      />
      <CardShell icon="key" title="Model provider">
        <div className="flex flex-col gap-3 px-3 pb-3 pt-1">
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Provider
            <select
              className={FIELD}
              value={backend}
              onChange={(e) => {
                const next = PROVIDERS.find((p) => p.id === e.target.value)!;
                setBackend(next.id);
                setModel(next.defaultModel);
                setApiKey("");
              }}
            >
              {PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Model
            <input className={FIELD} value={model} onChange={(e) => setModel(e.target.value)} />
          </label>
          {!provider.local && (
            <label className="flex flex-col gap-1 text-xs text-text-2">
              API key ({provider.keyEnvVar}) — leave blank to keep the stored key
              <input
                type="password"
                className={FIELD}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-…"
              />
            </label>
          )}
          <div className="flex items-center gap-2">
            <BtnPrimary
              disabled={busy || !model}
              onClick={() => {
                pendingSave.current = true;
                send({
                  type: "settings/setProvider",
                  backend,
                  model,
                  ...(provider.local || !apiKey ? {} : { apiKey }),
                });
              }}
            >
              {busy ? "Validating…" : "Save & validate"}
            </BtnPrimary>
            {savedFlash && (
              <span className="anim-pop flex items-center gap-1 text-[11px]" style={{ color: "var(--color-green)" }}>
                <Icon name="check" size={11} /> Saved
              </span>
            )}
          </div>
          {state.provider && (
            <p className="text-[11px] text-text-3">
              Active: <code>{state.provider.backend}</code> / <code>{state.provider.model}</code>
            </p>
          )}
        </div>
      </CardShell>
    </div>
  );
}
```

**Gotcha:** `<label>` wrapping input+text gives the accessible name — the tests use `getByLabelText("Model")`, which works with this wrapping-label markup.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/sections/ProviderSection.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.tsx apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.test.tsx
git commit -m "feat(webview): ProviderSection on CardShell with saved-flash feedback"
```

---

### Task 7: mcpEntry module + McpSection

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/mcpEntry.ts` (moved verbatim from `SettingsApp.tsx`)
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/McpSection.tsx`
- Test: `apps/vscode-extension/webview-ui/src/settings/mcpEntry.test.ts`
- Test: `apps/vscode-extension/webview-ui/src/settings/sections/McpSection.test.tsx`

**Interfaces:**
- Produces: `splitCommandLine(input: string): string[]`, `buildMcpEntry(input: { transport: McpTransport; commandLine: string; url: string; envVarNames: string[] }): Record<string, unknown>`, `type McpTransport = "stdio" | "http" | "sse"`; `McpSection(props: SectionProps)`.

- [ ] **Step 1: Write the failing tests**

```ts
// apps/vscode-extension/webview-ui/src/settings/mcpEntry.test.ts
import { describe, it, expect } from "vitest";
import { buildMcpEntry, splitCommandLine } from "./mcpEntry";

describe("splitCommandLine", () => {
  it("honors quoted segments containing spaces", () => {
    expect(splitCommandLine('uv run "/Users/x/AI editor/server.py"')).toEqual([
      "uv", "run", "/Users/x/AI editor/server.py",
    ]);
  });
});

describe("buildMcpEntry", () => {
  it("stdio: command/args + ${VAR} env refs", () => {
    expect(
      buildMcpEntry({ transport: "stdio", commandLine: "uv run s.py", url: "", envVarNames: ["OLLAMA_API_KEY"] }),
    ).toEqual({
      command: "uv", args: ["run", "s.py"], enabled: true,
      env: { OLLAMA_API_KEY: "${OLLAMA_API_KEY}" },
    });
  });

  it("http: first env var becomes a Bearer Authorization header", () => {
    expect(
      buildMcpEntry({ transport: "http", commandLine: "", url: "https://x", envVarNames: ["GITHUB_PAT"] }),
    ).toEqual({
      type: "http", url: "https://x", enabled: true,
      headers: { Authorization: "Bearer ${GITHUB_PAT}" },
    });
  });
});
```

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/McpSection.tsx.test — save as McpSection.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { McpSection } from "./McpSection";
import type { SettingsState } from "../types";

function makeState(overrides: Partial<SettingsState["mcp"]> = {}): SettingsState {
  return {
    provider: null,
    runtime: null,
    mcp: {
      enabled: true,
      servers: [
        { name: "web", transport: "stdio", enabledInFile: true, state: "connected", detail: null, toolCount: 2, userEnabled: true },
        { name: "gh", transport: "http", enabledInFile: true, state: "failed", detail: "missing GITHUB_PAT", toolCount: 0, userEnabled: true },
      ],
      ...overrides,
    },
    skills: [],
    envFlags: {},
    restartRequired: false,
  };
}

describe("McpSection", () => {
  it("renders server rows and toggles via the switch", () => {
    const send = vi.fn();
    render(<McpSection state={makeState()} busy={false} send={send} />);
    expect(screen.getByText("web")).toBeTruthy();
    expect(screen.getByText("missing GITHUB_PAT")).toBeTruthy();
    fireEvent.click(screen.getByRole("switch", { name: "Enable web" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/mcpToggle", name: "web", enabled: false });
  });

  it("filters servers via search", () => {
    render(<McpSection state={makeState()} busy={false} send={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("Type to search…"), { target: { value: "gh" } });
    expect(screen.queryByText("web")).toBeNull();
    expect(screen.getByText("gh")).toBeTruthy();
  });

  it("posts settings/mcpUpsert from the add-server form", () => {
    const send = vi.fn();
    render(<McpSection state={makeState()} busy={false} send={send} />);
    fireEvent.change(screen.getByPlaceholderText("name"), { target: { value: "docs" } });
    fireEvent.change(screen.getByPlaceholderText(/command line/), { target: { value: "uv run docs.py" } });
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(send).toHaveBeenCalledWith({
      type: "settings/mcpUpsert",
      name: "docs",
      entry: { command: "uv", args: ["run", "docs.py"], enabled: true },
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/mcpEntry.test.ts src/settings/sections/McpSection.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`mcpEntry.ts` — move `splitCommandLine`, `buildMcpEntry`, and the `McpTransport` type **verbatim** out of `SettingsApp.tsx` (keep the mirror-comments referencing `src/mcp-quickpick.ts`), adding exports:

```ts
// apps/vscode-extension/webview-ui/src/settings/mcpEntry.ts
export type McpTransport = "stdio" | "http" | "sse";

// Mirrors src/mcp-quickpick.ts::splitCommandLine — a bare \s+ split breaks on
// any quoted argument containing a space (e.g. a path under a directory named
// "AI editor") — this repo's own path is a real case.
export function splitCommandLine(input: string): string[] {
  const tokens: string[] = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(input)) !== null) {
    const token = match[1] ?? match[2] ?? match[3] ?? "";
    if (token) tokens.push(token);
  }
  return tokens;
}

// Mirrors src/mcp-quickpick.ts::buildMcpEntry — same assembly rules, a
// separate call site (webview form vs. QuickPick command).
export function buildMcpEntry(input: {
  transport: McpTransport;
  commandLine: string;
  url: string;
  envVarNames: string[];
}): Record<string, unknown> {
  if (input.transport === "stdio") {
    const [command, ...args] = splitCommandLine(input.commandLine.trim());
    const entry: Record<string, unknown> = { command, args, enabled: true };
    if (input.envVarNames.length) {
      entry.env = Object.fromEntries(input.envVarNames.map((v) => [v, `\${${v}}`]));
    }
    return entry;
  }
  const entry: Record<string, unknown> = { type: input.transport, url: input.url, enabled: true };
  if (input.envVarNames.length) {
    const [first, ...rest] = input.envVarNames;
    const headers: Record<string, string> = { Authorization: `Bearer \${${first}}` };
    for (const name of rest) headers[name] = `\${${name}}`;
    entry.headers = headers;
  }
  return entry;
}
```

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/McpSection.tsx
import { useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { Switch } from "../../components/shared/Switch";
import { SectionHeader } from "../SectionHeader";
import { buildMcpEntry, type McpTransport } from "../mcpEntry";
import { FIELD } from "../ui";
import type { SectionProps } from "./meta";

/** Colored status dot; the transient "connecting" state pulses a ring. */
function StatusDot({ state }: { state: string }) {
  const color =
    state === "connected" ? "var(--color-green)"
    : state === "connecting" ? "var(--color-amber)"
    : state === "failed" ? "var(--color-red)"
    : "var(--color-text-4)";
  return (
    <span
      aria-label={state}
      className="inline-block h-[7px] w-[7px] flex-shrink-0 rounded-full"
      style={{
        background: color,
        animation: state === "connecting" ? "dot-pulse 1.2s ease-in-out infinite" : undefined,
      }}
    />
  );
}

/** McpSection — server list (status dot, tool count, switch, reconnect/remove) + add form. */
export function McpSection({ state, busy, send }: SectionProps) {
  const [filter, setFilter] = useState("");
  const [transport, setTransport] = useState<McpTransport>("stdio");
  const [name, setName] = useState("");
  const [commandOrUrl, setCommandOrUrl] = useState("");
  const [envVars, setEnvVars] = useState("");

  const servers = state.mcp.servers.filter((s) =>
    s.name.toLowerCase().includes(filter.trim().toLowerCase()),
  );

  return (
    <div>
      <SectionHeader
        title="MCP Servers"
        description="External tool servers from .crucible/mcp.json. Every tool call is approval-gated in chat."
        search={{ value: filter, onChange: setFilter }}
      />

      {!state.mcp.enabled && (
        <p className="mb-3 text-[11px]" style={{ color: "var(--color-amber)" }}>
          MCP is disabled (CRUCIBLE_MCP_ENABLED=0) — servers below stay dormant.
        </p>
      )}

      <div className="flex flex-col gap-2.5">
        {servers.map((s, i) => (
          <div key={s.name} className="anim-section" style={{ animationDelay: `${i * 35}ms` }}>
            <CardShell
              icon="plug"
              title={s.name}
              subtitle={`${s.transport} · ${s.toolCount} tools`}
              badge={<StatusDot state={s.state} />}
              trailing={
                <span className="flex items-center gap-1.5">
                  <Switch
                    checked={s.userEnabled}
                    label={`Enable ${s.name}`}
                    onChange={(next) => send({ type: "settings/mcpToggle", name: s.name, enabled: next })}
                  />
                  <BtnGhost disabled={busy} onClick={() => send({ type: "settings/mcpReconnect", name: s.name })}>
                    Reconnect
                  </BtnGhost>
                  <BtnDanger disabled={busy} onClick={() => send({ type: "settings/mcpDelete", name: s.name })}>
                    Remove
                  </BtnDanger>
                </span>
              }
            >
              {s.detail && <p className="px-3 pb-2 text-[11px] text-text-3">{s.detail}</p>}
            </CardShell>
          </div>
        ))}
        {servers.length === 0 && (
          <p className="py-6 text-center text-[11px] text-text-3">
            {state.mcp.servers.length === 0 ? "No MCP servers configured." : "No servers match the search."}
          </p>
        )}
      </div>

      <div className="mt-4">
        <CardShell icon="plus" title="Add server">
          <div className="flex flex-col gap-2 px-3 pb-3 pt-1">
            <div className="flex gap-2">
              <input className={`${FIELD} w-40`} placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
              <select className={FIELD} value={transport} onChange={(e) => setTransport(e.target.value as McpTransport)}>
                <option value="stdio">stdio</option>
                <option value="http">http</option>
                <option value="sse">sse</option>
              </select>
            </div>
            <input
              className={FIELD}
              placeholder={transport === "stdio" ? "command line (e.g. uv run server.py)" : "url"}
              value={commandOrUrl}
              onChange={(e) => setCommandOrUrl(e.target.value)}
            />
            <input
              className={FIELD}
              placeholder="env var names, comma-separated (e.g. GITHUB_PAT)"
              value={envVars}
              onChange={(e) => setEnvVars(e.target.value)}
            />
            <BtnPrimary
              className="self-start"
              disabled={busy || !name || !commandOrUrl}
              onClick={() => {
                const envVarNames = envVars.split(",").map((v) => v.trim()).filter(Boolean);
                send({
                  type: "settings/mcpUpsert",
                  name,
                  entry: buildMcpEntry({ transport, commandLine: commandOrUrl, url: commandOrUrl, envVarNames }),
                });
                setName(""); setCommandOrUrl(""); setEnvVars("");
              }}
            >
              Add server
            </BtnPrimary>
          </div>
        </CardShell>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/mcpEntry.test.ts src/settings/sections/McpSection.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings/mcpEntry.ts apps/vscode-extension/webview-ui/src/settings/mcpEntry.test.ts apps/vscode-extension/webview-ui/src/settings/sections/McpSection.tsx apps/vscode-extension/webview-ui/src/settings/sections/McpSection.test.tsx
git commit -m "feat(webview): McpSection with status dots, switches, and search"
```

---

### Task 8: SkillsSection + PoliciesSection + RuntimeSection

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/SkillsSection.tsx`
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/PoliciesSection.tsx`
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/RuntimeSection.tsx`
- Test: `apps/vscode-extension/webview-ui/src/settings/sections/SimpleSections.test.tsx`

**Interfaces:**
- Consumes: `SectionProps`, `ENV_FLAG_OPTIONS` (settings/types.ts), `Switch`, `CardShell`, `BtnGhost`, `SectionHeader`, `FIELD`.
- Produces: `SkillsSection(props)`, `PoliciesSection(props)`, `RuntimeSection(props)`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/SimpleSections.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SkillsSection } from "./SkillsSection";
import { PoliciesSection } from "./PoliciesSection";
import { RuntimeSection } from "./RuntimeSection";
import type { SettingsState } from "../types";

const state: SettingsState = {
  provider: null,
  runtime: { releaseTag: "v0.3.0", components: { agentd: "0.3.0", indexer: "0.3.0" } },
  mcp: { enabled: false, servers: [] },
  skills: [
    { name: "brainstorming", description: "Explore ideas", enabled: true },
    { name: "systematic-debugging", description: "Debug carefully", enabled: false },
  ],
  envFlags: { "crucible.policy.shell": "ask" },
  restartRequired: false,
};

describe("SkillsSection", () => {
  it("toggles a skill and filters", () => {
    const send = vi.fn();
    render(<SkillsSection state={state} busy={false} send={send} />);
    fireEvent.click(screen.getByRole("switch", { name: "Enable brainstorming" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/skillToggle", name: "brainstorming", enabled: false });
    fireEvent.change(screen.getByPlaceholderText("Type to search…"), { target: { value: "debug" } });
    expect(screen.queryByText("brainstorming")).toBeNull();
  });
});

describe("PoliciesSection", () => {
  it("posts settings/setEnvFlag on change", () => {
    const send = vi.fn();
    render(<PoliciesSection state={state} busy={false} send={send} />);
    fireEvent.change(screen.getByLabelText("Shell command policy"), { target: { value: "allow_all" } });
    expect(send).toHaveBeenCalledWith({ type: "settings/setEnvFlag", key: "crucible.policy.shell", value: "allow_all" });
  });
});

describe("RuntimeSection", () => {
  it("shows versions and posts restart", () => {
    const send = vi.fn();
    render(<RuntimeSection state={state} busy={false} send={send} />);
    expect(screen.getByText(/v0\.3\.0/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Restart backend/ }));
    expect(send).toHaveBeenCalledWith({ type: "settings/restartBackend" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/sections/SimpleSections.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement the three sections**

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/SkillsSection.tsx
import { useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { Switch } from "../../components/shared/Switch";
import { SectionHeader } from "../SectionHeader";
import type { SectionProps } from "./meta";

/** SkillsSection — searchable list of discovered skills with enable switches. */
export function SkillsSection({ state, send }: SectionProps) {
  const [filter, setFilter] = useState("");
  const skills = state.skills.filter(
    (s) =>
      s.name.toLowerCase().includes(filter.trim().toLowerCase()) ||
      s.description.toLowerCase().includes(filter.trim().toLowerCase()),
  );

  return (
    <div>
      <SectionHeader
        title="Skills"
        description="SKILL.md folders discovered in .crucible/skills. Disabling a skill hides it from the agent (requires a backend restart)."
        search={{ value: filter, onChange: setFilter }}
      />
      <CardShell icon="bolt" title="Workspace skills" trailing={<span className="text-[10px] text-text-3">{state.skills.length}</span>}>
        <ul className="flex flex-col px-3 pb-2 pt-1">
          {skills.map((s, i) => (
            <li
              key={s.name}
              className="anim-section flex items-center gap-2.5 border-b py-2 last:border-b-0"
              style={{ borderColor: "var(--hairline)", animationDelay: `${i * 25}ms` }}
            >
              <Switch
                checked={s.enabled}
                label={`Enable ${s.name}`}
                onChange={(next) => send({ type: "settings/skillToggle", name: s.name, enabled: next })}
              />
              <span className="text-xs font-medium text-text">{s.name}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-text-3">{s.description}</span>
            </li>
          ))}
          {skills.length === 0 && (
            <li className="py-5 text-center text-[11px] text-text-3">
              {state.skills.length === 0 ? "No skills discovered in this workspace." : "No skills match the search."}
            </li>
          )}
        </ul>
      </CardShell>
    </div>
  );
}
```

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/PoliciesSection.tsx
import { CardShell } from "../../components/shared/CardShell";
import { SectionHeader } from "../SectionHeader";
import { ENV_FLAG_OPTIONS } from "../types";
import { FIELD } from "../ui";
import type { SectionProps } from "./meta";

/** PoliciesSection — env-flag dropdowns (shell/scope policy, memory flags). */
export function PoliciesSection({ state, send }: SectionProps) {
  return (
    <div>
      <SectionHeader
        title="Policies & Memory"
        description="Approval policies for shell commands and out-of-scope writes, plus the memory harness. Changes apply after a backend restart."
      />
      <CardShell icon="shield" title="Policies">
        <div className="flex flex-col px-3 pb-2 pt-1">
          {ENV_FLAG_OPTIONS.map((opt) => (
            <label
              key={opt.key}
              className="flex items-center justify-between gap-2 border-b py-2.5 text-xs text-text-2 last:border-b-0"
              style={{ borderColor: "var(--hairline)" }}
            >
              {opt.label}
              <select
                className={FIELD}
                value={state.envFlags[opt.key] ?? opt.options[0]}
                onChange={(e) => send({ type: "settings/setEnvFlag", key: opt.key, value: e.target.value })}
              >
                {opt.options.map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
      </CardShell>
    </div>
  );
}
```

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/RuntimeSection.tsx
import { CardShell } from "../../components/shared/CardShell";
import { BtnGhost } from "../../components/shared/buttons";
import { SectionHeader } from "../SectionHeader";
import type { SectionProps } from "./meta";

/** RuntimeSection — installed component versions + restart. */
export function RuntimeSection({ state, busy, send }: SectionProps) {
  return (
    <div>
      <SectionHeader
        title="Runtime"
        description="The managed runtime installed under ~/.crucible/runtime."
      />
      <CardShell
        icon="chip"
        title="Installed runtime"
        subtitle={state.runtime ? `release ${state.runtime.releaseTag}` : "not installed"}
      >
        <div className="flex flex-col gap-3 px-3 pb-3 pt-1">
          {state.runtime ? (
            <ul className="flex flex-col">
              {Object.entries(state.runtime.components).map(([id, version]) => (
                <li
                  key={id}
                  className="flex items-center justify-between border-b py-1.5 text-[11px] last:border-b-0"
                  style={{ borderColor: "var(--hairline)" }}
                >
                  <span className="text-text-2">{id}</span>
                  <span className="font-mono text-text-3">{version}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[11px] text-text-3">Runtime not installed — run "Crucible: Run Setup".</p>
          )}
          <BtnGhost className="self-start" disabled={busy} onClick={() => send({ type: "settings/restartBackend" })}>
            Restart backend
          </BtnGhost>
        </div>
      </CardShell>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/sections/SimpleSections.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings/sections/SkillsSection.tsx apps/vscode-extension/webview-ui/src/settings/sections/PoliciesSection.tsx apps/vscode-extension/webview-ui/src/settings/sections/RuntimeSection.tsx apps/vscode-extension/webview-ui/src/settings/sections/SimpleSections.test.tsx
git commit -m "feat(webview): Skills, Policies, and Runtime settings sections"
```

---

### Task 9: Instructions host path (AGENTS.md read/write)

**Files:**
- Create: `apps/vscode-extension/src/instructions-file.ts`
- Modify: `apps/vscode-extension/src/settings-data.ts` (deps + 2 message cases)
- Modify: `apps/vscode-extension/src/settings-panel.ts` (wire deps)
- Modify: `apps/vscode-extension/webview-ui/src/settings/types.ts` (mirror the new messages)
- Test: `apps/vscode-extension/test/instructions-file.test.ts`
- Test: `apps/vscode-extension/test/settings-data.test.ts` (extend)

**Interfaces:**
- Produces:
  - `loadInstructions(workspacePath: string): { content: string; exists: boolean }`
  - `saveInstructions(workspacePath: string, content: string): void`
  - `SettingsDeps` gains `readInstructions(): { content: string; exists: boolean }` and `writeInstructions(content: string): void`
  - New messages — webview→host: `{ type: "settings/loadInstructions" }`, `{ type: "settings/saveInstructions"; content: string }`; host→webview: `{ type: "settings/instructions"; content: string; exists: boolean }` (added to BOTH `src/settings-data.ts` and the webview mirror `webview-ui/src/settings/types.ts` — the three-mirror footgun class).

- [ ] **Step 1: Write the failing tests**

```ts
// apps/vscode-extension/test/instructions-file.test.ts
import { describe, expect, it } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loadInstructions, saveInstructions } from "../src/instructions-file.js";

function tmpWorkspace(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "instr-"));
}

describe("instructions-file", () => {
  it("returns exists:false and empty content when AGENTS.md is missing", () => {
    expect(loadInstructions(tmpWorkspace())).toEqual({ content: "", exists: false });
  });

  it("round-trips content", () => {
    const ws = tmpWorkspace();
    saveInstructions(ws, "# Rules\nBe kind.\n");
    expect(loadInstructions(ws)).toEqual({ content: "# Rules\nBe kind.\n", exists: true });
  });
});
```

Extend `apps/vscode-extension/test/settings-data.test.ts` — add (adapting to that file's existing fake-deps builder; if it has a `makeDeps()` helper, extend it with the two new fns):

```ts
describe("instructions messages", () => {
  it("settings/loadInstructions posts the file state", async () => {
    const posts: SettingsOutMsg[] = [];
    const deps = makeDeps({
      readInstructions: () => ({ content: "# hi", exists: true }),
      writeInstructions: () => {},
    });
    const handle = createSettingsHandler(deps, (m) => posts.push(m));
    await handle({ type: "settings/loadInstructions" });
    expect(posts).toContainEqual({ type: "settings/instructions", content: "# hi", exists: true });
  });

  it("settings/saveInstructions writes then echoes the saved state", async () => {
    const written: string[] = [];
    const posts: SettingsOutMsg[] = [];
    const deps = makeDeps({
      readInstructions: () => ({ content: "", exists: false }),
      writeInstructions: (c: string) => written.push(c),
    });
    const handle = createSettingsHandler(deps, (m) => posts.push(m));
    await handle({ type: "settings/saveInstructions", content: "# new" });
    expect(written).toEqual(["# new"]);
    expect(posts).toContainEqual({ type: "settings/instructions", content: "# new", exists: true });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run -w crucible-vscode-extension test -- instructions-file settings-data`
Expected: FAIL — module/type errors.

- [ ] **Step 3: Implement**

```ts
// apps/vscode-extension/src/instructions-file.ts
import * as fs from "node:fs";
import * as path from "node:path";

// vscode-free AGENTS.md access for the Settings "Instructions" section (the
// prompt-files.ts direct-fs pattern). The backend's ProjectInstructionsLoader
// mtime-watches the same file, so a save here is picked up on the next turn
// with no coordination.

export function loadInstructions(workspacePath: string): { content: string; exists: boolean } {
  try {
    return { content: fs.readFileSync(path.join(workspacePath, "AGENTS.md"), "utf8"), exists: true };
  } catch {
    return { content: "", exists: false };
  }
}

export function saveInstructions(workspacePath: string, content: string): void {
  fs.writeFileSync(path.join(workspacePath, "AGENTS.md"), content, "utf8");
}
```

`src/settings-data.ts` — add to `SettingsInMsg`:

```ts
  | { type: "settings/loadInstructions" }
  | { type: "settings/saveInstructions"; content: string }
```

to `SettingsOutMsg`:

```ts
  | { type: "settings/instructions"; content: string; exists: boolean }
```

to `SettingsDeps`:

```ts
  readInstructions(): { content: string; exists: boolean };
  writeInstructions(content: string): void;
```

and two cases in the handler switch (before the closing brace):

```ts
        case "settings/loadInstructions": {
          post({ type: "settings/instructions", ...deps.readInstructions() });
          return;
        }
        case "settings/saveInstructions": {
          deps.writeInstructions(msg.content);
          post({ type: "settings/instructions", content: msg.content, exists: true });
          return;
        }
```

`src/settings-panel.ts` — in `buildDeps()` add:

```ts
      readInstructions: () => loadInstructions(this.workspacePath),
      writeInstructions: (content) => saveInstructions(this.workspacePath, content),
```

with the import `import { loadInstructions, saveInstructions } from "./instructions-file.js";`

`webview-ui/src/settings/types.ts` — mirror the same three message additions verbatim.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run -w crucible-vscode-extension test -- instructions-file settings-data && npm run -w crucible-vscode-extension typecheck`
Expected: PASS, clean typecheck.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/instructions-file.ts apps/vscode-extension/src/settings-data.ts apps/vscode-extension/src/settings-panel.ts apps/vscode-extension/webview-ui/src/settings/types.ts apps/vscode-extension/test/instructions-file.test.ts apps/vscode-extension/test/settings-data.test.ts
git commit -m "feat(extension): AGENTS.md read/write path for the settings Instructions section"
```

---

### Task 10: InstructionsSection (webview)

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/InstructionsSection.tsx`
- Test: `apps/vscode-extension/webview-ui/src/settings/sections/InstructionsSection.test.tsx`

**Interfaces:**
- Produces: `InstructionsSection({ instructions, busy, send }: { instructions: { content: string; exists: boolean } | null; busy: boolean; send: (msg: SettingsInMsg) => void })`. The SettingsApp shell (Task 11) owns fetching (`settings/loadInstructions` on first visit) and holds the `instructions` state from `settings/instructions` replies.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/InstructionsSection.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { InstructionsSection } from "./InstructionsSection";

describe("InstructionsSection", () => {
  it("shows a loading state before the file arrives", () => {
    render(<InstructionsSection instructions={null} busy={false} send={vi.fn()} />);
    expect(screen.getByText(/Loading/)).toBeTruthy();
  });

  it("empty state offers Create, which saves an empty file", () => {
    const send = vi.fn();
    render(<InstructionsSection instructions={{ content: "", exists: false }} busy={false} send={send} />);
    expect(screen.getByText(/No AGENTS\.md yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Create AGENTS\.md/ }));
    expect(send).toHaveBeenCalledWith({ type: "settings/saveInstructions", content: "" });
  });

  it("editor enables Save only when dirty, then posts the content", () => {
    const send = vi.fn();
    render(<InstructionsSection instructions={{ content: "# a", exists: true }} busy={false} send={send} />);
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "# a\n- rule" } });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);
    expect(send).toHaveBeenCalledWith({ type: "settings/saveInstructions", content: "# a\n- rule" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/sections/InstructionsSection.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// apps/vscode-extension/webview-ui/src/settings/sections/InstructionsSection.tsx
import { useEffect, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnPrimary } from "../../components/shared/buttons";
import { Icon } from "../../components/Icon";
import { SectionHeader } from "../SectionHeader";
import type { SettingsInMsg } from "../types";

interface Props {
  instructions: { content: string; exists: boolean } | null;
  busy: boolean;
  send: (msg: SettingsInMsg) => void;
}

/**
 * InstructionsSection — a plain AGENTS.md editor. The backend mtime-watches
 * the file, so a save here applies from the next turn with no restart.
 */
export function InstructionsSection({ instructions, busy, send }: Props) {
  const [draft, setDraft] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);

  // Sync the draft whenever a fresh file state arrives (load or post-save echo).
  useEffect(() => {
    if (instructions) setDraft(instructions.content);
  }, [instructions?.content, instructions?.exists]);

  const header = (
    <SectionHeader
      title="Instructions"
      description="Project instructions the agent reads on every turn (AGENTS.md at the workspace root). Saves apply from the next message — no restart."
    />
  );

  if (!instructions) {
    return (
      <div>
        {header}
        <p className="py-8 text-center text-[11px] text-text-3">Loading AGENTS.md…</p>
      </div>
    );
  }

  if (!instructions.exists) {
    return (
      <div>
        {header}
        <CardShell icon="book" title="AGENTS.md">
          <div className="flex flex-col items-center gap-3 px-3 py-8">
            <span
              className="flex h-10 w-10 items-center justify-center rounded-[10px]"
              style={{ background: "var(--accent-bg)", color: "var(--color-accent)" }}
            >
              <Icon name="book" size={18} />
            </span>
            <p className="text-xs font-semibold text-text">No AGENTS.md yet in this workspace</p>
            <p className="max-w-[340px] text-center text-[11px] leading-relaxed text-text-3">
              Create one to give the agent always-on project rules — conventions, commands, gotchas.
            </p>
            <BtnPrimary icon="plus" onClick={() => send({ type: "settings/saveInstructions", content: "" })}>
              Create AGENTS.md
            </BtnPrimary>
          </div>
        </CardShell>
      </div>
    );
  }

  const dirty = draft !== instructions.content;

  return (
    <div>
      {header}
      <CardShell
        icon="book"
        title="AGENTS.md"
        badge={dirty ? (
          <span className="h-[6px] w-[6px] rounded-full" style={{ background: "var(--color-amber)" }} aria-label="unsaved changes" />
        ) : undefined}
      >
        <div className="flex flex-col gap-2.5 px-3 pb-3 pt-1">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            className="min-h-[320px] w-full resize-y rounded-md border border-border-strong bg-surface-2 p-2.5 text-[11.5px] leading-relaxed text-text outline-none transition-colors duration-150 focus:border-[var(--color-accent)]"
            style={{ fontFamily: "var(--vscode-editor-font-family, ui-monospace, Menlo, monospace)" }}
          />
          <div className="flex items-center gap-2">
            <BtnPrimary
              disabled={busy || !dirty}
              onClick={() => {
                send({ type: "settings/saveInstructions", content: draft });
                setSavedFlash(true);
                setTimeout(() => setSavedFlash(false), 2000);
              }}
            >
              Save
            </BtnPrimary>
            {savedFlash && (
              <span className="anim-pop flex items-center gap-1 text-[11px]" style={{ color: "var(--color-green)" }}>
                <Icon name="check" size={11} /> Saved — applies next turn
              </span>
            )}
          </div>
        </div>
      </CardShell>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/sections/InstructionsSection.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings/sections/InstructionsSection.tsx apps/vscode-extension/webview-ui/src/settings/sections/InstructionsSection.test.tsx
git commit -m "feat(webview): Instructions section — AGENTS.md editor with empty state"
```

---

### Task 11: SettingsApp shell assembly

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/settings/SettingsApp.tsx` (full rewrite to a thin shell)
- Test: `apps/vscode-extension/webview-ui/src/settings/SettingsApp.test.tsx`

**Interfaces:**
- Consumes: everything from Tasks 3–10.
- Produces: the final Settings surface — NavRail + animated content pane + global banners. The old flat markup (and its inline `splitCommandLine`/`buildMcpEntry`) is deleted.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/settings/SettingsApp.test.tsx
import { render, screen, fireEvent, act } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import SettingsApp from "./SettingsApp";
import { vscode } from "./vscodeApi";
import type { SettingsState } from "./types";

vi.mock("./vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const state: SettingsState = {
  provider: { backend: "gemini", model: "gemini-flash-latest" },
  runtime: null,
  mcp: { enabled: true, servers: [] },
  skills: [{ name: "s1", description: "d", enabled: true }],
  envFlags: {},
  restartRequired: true,
};

function deliver(data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data }));
  });
}

describe("SettingsApp shell", () => {
  beforeEach(() => vi.clearAllMocks());

  it("posts settings/load on mount, lands on Overview, and navigates", () => {
    render(<SettingsApp />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/load" });
    deliver({ type: "settings/state", state });
    // Overview grid visible
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
    // Navigate via the rail
    fireEvent.click(screen.getByRole("button", { name: /Provider/ }));
    expect(screen.getByRole("heading", { name: "Provider" })).toBeTruthy();
  });

  it("requests instructions on first visit to the Instructions section", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/state", state });
    fireEvent.click(screen.getByRole("button", { name: /Instructions/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "settings/loadInstructions" });
    deliver({ type: "settings/instructions", content: "# rules", exists: true });
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("# rules");
  });

  it("shows the global restart banner in every section", () => {
    render(<SettingsApp />);
    deliver({ type: "settings/state", state });
    expect(screen.getByText(/require a backend restart/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Skills/ }));
    expect(screen.getByText(/require a backend restart/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/settings/SettingsApp.test.tsx`
Expected: FAIL — the old flat SettingsApp has no Overview heading/nav buttons.

- [ ] **Step 3: Rewrite SettingsApp**

```tsx
// apps/vscode-extension/webview-ui/src/settings/SettingsApp.tsx
import { useEffect, useRef, useState } from "react";
import { BtnPrimary } from "../components/shared/buttons";
import { NavRail } from "./NavRail";
import { OverviewSection } from "./sections/OverviewSection";
import { ProviderSection } from "./sections/ProviderSection";
import { McpSection } from "./sections/McpSection";
import { SkillsSection } from "./sections/SkillsSection";
import { InstructionsSection } from "./sections/InstructionsSection";
import { PoliciesSection } from "./sections/PoliciesSection";
import { RuntimeSection } from "./sections/RuntimeSection";
import type { SectionId, SectionProps } from "./sections/meta";
import type { SettingsOutMsg, SettingsState } from "./types";
import { vscode } from "./vscodeApi";

/**
 * SettingsApp — thin shell: NavRail + one active section in an animated
 * content pane. Data flow is unchanged: `settings/load` populates one
 * SettingsState snapshot, every mutating action posts and receives a
 * rebuilt snapshot. Sections are pure presentation over {state, busy, send}.
 */
export default function SettingsApp() {
  const [state, setState] = useState<SettingsState | null>(null);
  const [instructions, setInstructions] = useState<{ content: string; exists: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [section, setSection] = useState<SectionId>("overview");
  const instructionsRequested = useRef(false);

  useEffect(() => {
    const onMessage = (event: MessageEvent<SettingsOutMsg>) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;
      setBusy(false);
      if (msg.type === "settings/state") {
        setState(msg.state);
        setError(null);
      } else if (msg.type === "settings/instructions") {
        setInstructions({ content: msg.content, exists: msg.exists });
      } else if (msg.type === "settings/error") {
        setError(msg.message);
      }
    };
    window.addEventListener("message", onMessage);
    vscode.postMessage({ type: "settings/load" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Lazy-load AGENTS.md the first time the Instructions section opens.
  useEffect(() => {
    if (section === "instructions" && !instructionsRequested.current) {
      instructionsRequested.current = true;
      vscode.postMessage({ type: "settings/loadInstructions" });
    }
  }, [section]);

  const send: SectionProps["send"] = (msg) => {
    setBusy(true);
    setError(null);
    vscode.postMessage(msg);
  };

  if (!state) {
    return <div className="p-6 text-sm text-text-3">Loading settings…</div>;
  }

  const props: SectionProps = { state, busy, send };
  const counts: Partial<Record<SectionId, number>> = {
    mcp: state.mcp.servers.length,
    skills: state.skills.length,
  };

  return (
    <div className="flex h-full min-h-0">
      <NavRail active={section} counts={counts} onSelect={setSection} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[560px] p-5">
          {error && (
            <div
              className="anim-slide-down mb-4 rounded-[10px] border px-3 py-2 text-xs"
              style={{ borderColor: "var(--red-brd)", background: "var(--red-bg)", color: "var(--color-red)" }}
            >
              {error}
            </div>
          )}
          {state.restartRequired && (
            <div
              className="anim-slide-down mb-4 flex items-center justify-between gap-3 rounded-[10px] border px-3 py-2 text-xs"
              style={{ borderColor: "rgba(251,191,36,.3)", background: "var(--amber-bg)", color: "var(--color-amber)" }}
            >
              <span>Some changes require a backend restart to take effect.</span>
              <BtnPrimary disabled={busy} onClick={() => send({ type: "settings/restartBackend" })}>
                Restart backend
              </BtnPrimary>
            </div>
          )}
          <div key={section} className="anim-section">
            {section === "overview" && <OverviewSection state={state} onNavigate={setSection} />}
            {section === "provider" && <ProviderSection {...props} />}
            {section === "mcp" && <McpSection {...props} />}
            {section === "skills" && <SkillsSection {...props} />}
            {section === "instructions" && (
              <InstructionsSection instructions={instructions} busy={busy} send={send} />
            )}
            {section === "policies" && <PoliciesSection {...props} />}
            {section === "runtime" && <RuntimeSection {...props} />}
          </div>
        </div>
      </main>
    </div>
  );
}
```

- [ ] **Step 4: Run the full webview suite (catches regressions in moved code)**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run && npx tsc --noEmit`
Expected: ALL PASS, clean typecheck.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings/SettingsApp.tsx apps/vscode-extension/webview-ui/src/settings/SettingsApp.test.tsx
git commit -m "feat(webview): SettingsApp left-nav shell with animated section pane"
```

---

### Task 12: Setup wizard reskin (StepRail + SetupApp)

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/setup/StepRail.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/setup/SetupApp.tsx`
- Test: `apps/vscode-extension/webview-ui/src/setup/SetupApp.test.tsx`

**Interfaces:**
- Consumes: `CardShell`, `BtnPrimary`, `Icon`, `FIELD` (import from `../settings/ui`), motion classes.
- Produces: `StepRail({ current: Step })` where `type Step = "welcome" | "install" | "provider" | "done"` (unchanged union, exported from `SetupApp.tsx` — move it to `StepRail.tsx` and import back to avoid a cycle: `StepRail.tsx` owns `export type Step`).

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/setup/SetupApp.test.tsx
import { render, screen, fireEvent, act } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import SetupApp from "./SetupApp";
import { vscode } from "./vscodeApi";

vi.mock("./vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

function deliver(data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data }));
  });
}

describe("SetupApp", () => {
  beforeEach(() => vi.clearAllMocks());

  it("walks welcome → install → provider → done", () => {
    render(<SetupApp />);
    // StepRail present
    expect(screen.getByLabelText(/Step 1 of 4/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/install" });
    deliver({ type: "setup/progress", component: "uv", status: "done" });
    deliver({ type: "setup/installDone", ok: true });
    // Provider step
    expect(screen.getByLabelText("Model")).toBeTruthy();
    deliver({ type: "setup/ready", port: 8090 });
    expect(screen.getByText(/8090/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Open chat/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setup/openChat" });
  });

  it("shows Retry when install fails", () => {
    render(<SetupApp />);
    fireEvent.click(screen.getByRole("button", { name: /Install runtime/ }));
    deliver({ type: "setup/progress", component: "agentd", status: "failed", detail: "pip exploded" });
    deliver({ type: "setup/installDone", ok: false });
    expect(screen.getByText("pip exploded")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/setup/SetupApp.test.tsx`
Expected: FAIL — old markup has no `Step 1 of 4` StepRail.

- [ ] **Step 3: Implement StepRail**

```tsx
// apps/vscode-extension/webview-ui/src/setup/StepRail.tsx
import { Fragment } from "react";
import { Icon } from "../components/Icon";

export type Step = "welcome" | "install" | "provider" | "done";

const STEP_ORDER: Step[] = ["welcome", "install", "provider", "done"];
const STEP_LABELS: Record<Step, string> = {
  welcome: "Welcome",
  install: "Install",
  provider: "Provider",
  done: "Ready",
};

/**
 * StepRail — 4 numbered dots joined by connector lines. Completed dots turn
 * green with a pop-in check, the active dot breathes with the accent glow,
 * lines fill with accent as steps complete.
 */
export function StepRail({ current }: { current: Step }) {
  const idx = STEP_ORDER.indexOf(current);
  return (
    <div className="mb-6 flex items-center" aria-label={`Step ${idx + 1} of 4`}>
      {STEP_ORDER.map((s, i) => {
        const isDone = i < idx;
        const isActive = i === idx;
        return (
          <Fragment key={s}>
            {i > 0 && (
              <span
                aria-hidden="true"
                className="mx-1.5 h-[2px] flex-1 rounded"
                style={{
                  background: i <= idx ? "var(--color-accent-deep)" : "var(--color-surface-3)",
                  transition: "background var(--dur-slow) var(--ease-out)",
                }}
              />
            )}
            <span className="flex items-center gap-1.5">
              <span
                className={[
                  "flex h-[18px] w-[18px] items-center justify-center rounded-full font-semibold",
                  isDone ? "anim-pop" : "",
                ].join(" ")}
                style={{
                  fontSize: "9.5px",
                  ...(isDone
                    ? { background: "var(--green-bg)", border: "1px solid var(--green-brd)", color: "var(--color-green)" }
                    : isActive
                      ? {
                          background: "linear-gradient(180deg, var(--color-accent-deep), var(--color-accent-hot))",
                          color: "#fff",
                          animation: "breathe 2.4s ease-in-out infinite",
                        }
                      : { background: "var(--color-surface-3)", color: "var(--color-text-3)" }),
                }}
              >
                {isDone ? <Icon name="check" size={9} /> : i + 1}
              </span>
              <span className="text-[10.5px]" style={{ color: isActive ? "var(--color-text)" : "var(--color-text-3)" }}>
                {STEP_LABELS[s]}
              </span>
            </span>
          </Fragment>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Reskin SetupApp**

Rewrite `SetupApp.tsx` keeping ALL existing state/handler logic (`onMessage` switch, `startInstall`, `saveAndStart`, the `Step` flow) byte-for-byte where possible — only the returned JSX changes. New body:

```tsx
// SetupApp.tsx — imports change to:
import { useEffect, useMemo, useState } from "react";
import { CardShell } from "../components/shared/CardShell";
import { BtnGhost, BtnPrimary } from "../components/shared/buttons";
import { Icon } from "../components/Icon";
import { FIELD } from "../settings/ui";
import { StepRail, type Step } from "./StepRail";
import { COMPONENT_LABELS, PROVIDERS, type SetupOutMsg } from "./types";
import { vscode } from "./vscodeApi";
```

(delete the local `type Step` — it now comes from StepRail). Replace `statusIcon` with an animated row icon:

```tsx
function StatusGlyph({ status }: { status: string }) {
  switch (status) {
    case "running":
      return (
        <span
          className="inline-block rounded-full border-2"
          style={{
            width: 10, height: 10,
            borderColor: "var(--color-accent-ink) var(--accent-bg) var(--accent-bg) var(--accent-bg)",
            animation: "spin 0.75s linear infinite",
          }}
          aria-label="running"
        />
      );
    case "done":
      return <span className="anim-pop inline-flex" style={{ color: "var(--color-green)" }} aria-label="done"><Icon name="check" size={12} /></span>;
    case "failed":
      return <span className="anim-pop inline-flex" style={{ color: "var(--color-red)" }} aria-label="failed"><Icon name="x" size={12} /></span>;
    case "skipped":
      return <span style={{ color: "var(--color-amber)" }} aria-label="skipped"><Icon name="chev-r" size={12} /></span>;
    default:
      return <span className="text-text-4">·</span>;
  }
}
```

Return JSX (state logic above it unchanged):

```tsx
  return (
    <div className="mx-auto flex max-w-xl flex-col gap-4 p-6 text-sm">
      <StepRail current={step} />

      {step === "welcome" && (
        <div className="anim-section flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <span
              className="flex h-11 w-11 items-center justify-center rounded-[12px]"
              style={{
                background: "linear-gradient(180deg, var(--color-accent-deep), var(--color-accent-hot))",
                color: "#fff",
                animation: "breathe 2.4s ease-in-out infinite",
              }}
            >
              <Icon name="spark" size={20} />
            </span>
            <div>
              <h1 className="text-base font-semibold text-text">AI Editor Setup</h1>
              <p className="text-xs text-text-3">Provision the local runtime, pick a model provider, start chatting.</p>
            </div>
          </div>
          <CardShell icon="chip" title="What gets installed">
            <ul className="flex flex-col px-3 pb-3 pt-1">
              {COMPONENT_ORDER.map((id, i) => (
                <li
                  key={id}
                  className="anim-section flex items-center gap-2 border-b py-2 text-xs text-text-2 last:border-b-0"
                  style={{ borderColor: "var(--hairline)", animationDelay: `${i * 45}ms` }}
                >
                  <Icon name="check" size={10} className="text-text-4" />
                  {COMPONENT_LABELS[id] ?? id}
                </li>
              ))}
            </ul>
            <p className="px-3 pb-3 text-[11px] text-text-3">
              Everything lands in <code>~/.crucible/runtime</code> — nothing touches your system Python or PATH.
            </p>
          </CardShell>
          <BtnPrimary className="self-start" icon="bolt" onClick={startInstall}>
            Install runtime
          </BtnPrimary>
        </div>
      )}

      {step === "install" && (
        <div className="anim-section">
          <CardShell icon="chip" title="Installing components">
            <ul className="flex flex-col px-3 pb-3 pt-1">
              {COMPONENT_ORDER.map((id) => {
                const row = progress[id];
                return (
                  <li key={id} className="flex items-center gap-2.5 border-b py-2 last:border-b-0" style={{ borderColor: "var(--hairline)" }}>
                    <span className="flex w-4 justify-center"><StatusGlyph status={row?.status ?? "pending"} /></span>
                    <span className="text-xs text-text">{COMPONENT_LABELS[id] ?? id}</span>
                    {row?.detail && <span className="min-w-0 flex-1 truncate text-[11px] text-text-3">{row.detail}</span>}
                  </li>
                );
              })}
            </ul>
            {installOk === false && (
              <div className="px-3 pb-3">
                <BtnPrimary icon="retry" onClick={startInstall}>Retry</BtnPrimary>
              </div>
            )}
          </CardShell>
        </div>
      )}

      {step === "provider" && (
        <div className="anim-section">
          <CardShell icon="key" title="Choose a model provider">
            <div className="flex flex-col gap-3 px-3 pb-3 pt-1">
              <label className="flex flex-col gap-1 text-xs text-text-2">
                Provider
                <select
                  className={FIELD}
                  value={backend}
                  onChange={(e) => {
                    const next = PROVIDERS.find((p) => p.id === e.target.value)!;
                    setBackend(next.id);
                    setModel(next.defaultModel);
                    setApiKey("");
                    setError(null);
                  }}
                >
                  {PROVIDERS.map((p) => (
                    <option key={p.id} value={p.id}>{p.label}</option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-text-2">
                Model
                <input className={FIELD} value={model} onChange={(e) => setModel(e.target.value)} />
              </label>
              {!provider.local && (
                <label className="flex flex-col gap-1 text-xs text-text-2">
                  API key ({provider.keyEnvVar})
                  <input type="password" className={FIELD} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-…" />
                </label>
              )}
              {provider.local && (
                <p className="text-[11px] text-text-3">Local provider — reachability is checked when the backend starts.</p>
              )}
              {error && <p className="text-[11px]" style={{ color: "var(--color-red)" }}>{error}</p>}
              <BtnPrimary className="self-start" disabled={busy || !model || (!provider.local && !apiKey)} onClick={saveAndStart}>
                {busy ? "Starting…" : "Save & Start"}
              </BtnPrimary>
            </div>
          </CardShell>
        </div>
      )}

      {step === "done" && (
        <div className="anim-section">
          <CardShell icon="check" iconColor="var(--color-green)" title="Ready" borderColor="var(--green-brd)">
            <div className="flex flex-col items-start gap-3 px-3 pb-3 pt-1">
              <p className="text-xs text-text-2">
                Backend is running on port {port}. Provider <code>{backend}</code> / <code>{model}</code> validated.
              </p>
              <BtnPrimary icon="send" onClick={() => vscode.postMessage({ type: "setup/openChat" })}>
                Open chat
              </BtnPrimary>
            </div>
          </CardShell>
        </div>
      )}
    </div>
  );
```

- [ ] **Step 5: Run tests + typecheck**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/setup/SetupApp.test.tsx && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/setup/StepRail.tsx apps/vscode-extension/webview-ui/src/setup/SetupApp.tsx apps/vscode-extension/webview-ui/src/setup/SetupApp.test.tsx
git commit -m "feat(webview): setup wizard reskin — StepRail progress, CardShell steps, animated install rows"
```

---

### Task 13: Composer model-swap host path

**Files:**
- Create: `apps/vscode-extension/src/composer-models.ts`
- Modify: `apps/vscode-extension/src/runtime/vscode-runtime.ts` (add `getProviderKey`)
- Modify: `apps/vscode-extension/src/controller.ts` (add `configClient()`)
- Modify: `apps/vscode-extension/src/chat-panel.ts` (+3 constructor callbacks, +3 message cases)
- Modify: `apps/vscode-extension/src/extension.ts` (wiring)
- Test: `apps/vscode-extension/test/composer-models.test.ts`

**Interfaces:**
- Produces:
  - `interface ModelOption { backend: string; label: string; model: string; active: boolean }`
  - `buildModelOptions(current: { backend: string; model: string } | null, keyedBackends: string[], providers: ProviderInfo[]): ModelOption[]` — keyed providers plus the current backend (even if local/unkeyed); the current backend's option carries the *live* model and `active: true`; others carry their `defaultModel`.
  - Webview→host messages: `{ type: "listModels" }`, `{ type: "setModel"; backend: string; model: string }`, `{ type: "openSettings" }`.
  - Host→webview replies: `{ type: "modelList"; current: { backend; model } | null; options: ModelOption[] }`, `{ type: "modelSwapError"; message: string }`.
  - `RuntimeManager.getProviderKey(backend: string): Promise<string | undefined>`
  - `AiEditorController.configClient(): BackendTaskClient` (public; body: `return this.createClient(this.settings.getBackendBaseUrl())` — same session-independent pattern as the private `memoryClient()`).

- [ ] **Step 1: Write the failing test**

```ts
// apps/vscode-extension/test/composer-models.test.ts
import { describe, expect, it } from "vitest";
import { buildModelOptions } from "../src/composer-models.js";
import { PROVIDERS } from "../src/setup-data.js";

describe("buildModelOptions", () => {
  it("offers only keyed providers, marking the current one active with its live model", () => {
    const options = buildModelOptions(
      { backend: "gemini", model: "gemini-flash-latest" },
      ["gemini", "anthropic"],
      PROVIDERS,
    );
    const ids = options.map((o) => o.backend);
    expect(ids).toEqual(["anthropic", "gemini"].sort((a, b) => ids.indexOf(a) - ids.indexOf(b)).length === 2 ? ids : ids); // order = PROVIDERS order
    expect(ids).toContain("gemini");
    expect(ids).toContain("anthropic");
    expect(ids).not.toContain("openai");
    const gemini = options.find((o) => o.backend === "gemini")!;
    expect(gemini).toMatchObject({ model: "gemini-flash-latest", active: true });
    const anthropic = options.find((o) => o.backend === "anthropic")!;
    expect(anthropic.active).toBe(false);
    expect(anthropic.model).toBe(PROVIDERS.find((p) => p.id === "anthropic")!.defaultModel);
  });

  it("includes an unkeyed local provider only when it is current", () => {
    const withCurrent = buildModelOptions({ backend: "ollama", model: "qwen3:8b" }, [], PROVIDERS);
    expect(withCurrent).toEqual([{ backend: "ollama", label: "Ollama (local)", model: "qwen3:8b", active: true }]);
    const without = buildModelOptions(null, [], PROVIDERS);
    expect(without).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run -w crucible-vscode-extension test -- composer-models`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `composer-models.ts`**

```ts
// apps/vscode-extension/src/composer-models.ts
import type { ProviderInfo } from "./setup-data.js";

// vscode-free assembly of the composer model-dropdown options. Only providers
// with a stored API key are offered (spec: never offer a hot-swap that is
// guaranteed to fail validation); the currently-active backend is always
// included — even a local/unkeyed one — since it is validated by definition.

export interface ModelOption {
  backend: string;
  label: string;
  model: string;
  active: boolean;
}

export function buildModelOptions(
  current: { backend: string; model: string } | null,
  keyedBackends: string[],
  providers: ProviderInfo[],
): ModelOption[] {
  const keyed = new Set(keyedBackends);
  return providers
    .filter((p) => keyed.has(p.id) || p.id === current?.backend)
    .map((p) => ({
      backend: p.id,
      label: p.label,
      model: p.id === current?.backend ? current.model : p.defaultModel,
      active: p.id === current?.backend,
    }));
}
```

Check `src/setup-data.ts` exports `ProviderInfo` and `PROVIDERS`; if `ProviderInfo` isn't exported yet, export it.

- [ ] **Step 4: Add the host plumbing**

`src/runtime/vscode-runtime.ts` — next to `storeProviderKey` (~line 174):

```ts
  /** Stored API key for a backend, if the user ever validated one. */
  async getProviderKey(backend: string): Promise<string | undefined> {
    return (await this.context.secrets.get(`crucible.providerKey.${backend}`)) ?? undefined;
  }
```

(If the ~line-158 code reads the same secret inline, refactor it to call this.)

`src/controller.ts` — near `memoryClient()` (~line 273):

```ts
  /** Session-independent client for config reads/hot-swap (composer model menu). */
  configClient(): BackendTaskClient {
    return this.createClient(this.settings.getBackendBaseUrl());
  }
```

`src/chat-panel.ts` — three new constructor params after `onDocDecision` (follow the existing positional-callback style):

```ts
    private readonly onListModels: () => Promise<{ current: { backend: string; model: string } | null; options: unknown[] }>,
    private readonly onSetModel: (backend: string, model: string) => Promise<{ current: { backend: string; model: string } | null; options: unknown[] }>,
    private readonly onOpenSettings: () => void,
```

and message cases before the final `else`:

```ts
      } else if (m["type"] === "listModels") {
        p = (async () => {
          const result = await this.onListModels();
          this.panel?.webview.postMessage({ type: "modelList", ...result });
        })();
      } else if (m["type"] === "setModel") {
        p = (async () => {
          try {
            const result = await this.onSetModel(m["backend"] as string, m["model"] as string);
            this.panel?.webview.postMessage({ type: "modelList", ...result });
          } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.panel?.webview.postMessage({ type: "modelSwapError", message });
          }
        })();
      } else if (m["type"] === "openSettings") {
        this.onOpenSettings();
        return;
```

(the `setModel` case handles its own errors so the generic `p.catch` never re-enables input for it — a swap failure must not wedge the composer.)

`src/extension.ts` — after the existing 26 ChatPanel callbacks add three (imports: `buildModelOptions` from `./composer-models.js`, `PROVIDERS` from `./setup-data.js`, `PROVIDER_KEY_ENV` from `./runtime/vscode-runtime.js`):

```ts
    async () => {
      const config = await controller.configClient().getConfig();
      const keyed: string[] = [];
      for (const p of PROVIDERS) {
        if (p.keyEnvVar && (await runtimeManager.getProviderKey(p.id)) !== undefined) keyed.push(p.id);
      }
      const current = config.provider ?? null;
      return { current, options: buildModelOptions(current, keyed, PROVIDERS) };
    },
    async (backend, model) => {
      // Pass the stored key as request credentials: the running backend's env may
      // predate this key (factory.py: request credentials override process env).
      const key = await runtimeManager.getProviderKey(backend);
      const envVar = PROVIDER_KEY_ENV[backend];
      const credentials = envVar && key ? { [envVar]: key } : undefined;
      await controller.configClient().setProvider({ backend, model, ...(credentials ? { credentials } : {}) });
      await runtimeManager.saveProvider(backend, model);
      const config = await controller.configClient().getConfig();
      const keyed: string[] = [];
      for (const p of PROVIDERS) {
        if (p.keyEnvVar && (await runtimeManager.getProviderKey(p.id)) !== undefined) keyed.push(p.id);
      }
      const current = config.provider ?? null;
      return { current, options: buildModelOptions(current, keyed, PROVIDERS) };
    },
    () => {
      void vscode.commands.executeCommand("crucible.openSettingsPanel");
    }
```

Extract the duplicated snapshot block into a local `async function composerModelState()` above the `new ChatPanel(...)` call and use it in both callbacks (DRY).

- [ ] **Step 5: Run tests + typecheck**

Run: `npm run -w crucible-vscode-extension test -- composer-models && npm run -w crucible-vscode-extension typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/src/composer-models.ts apps/vscode-extension/src/runtime/vscode-runtime.ts apps/vscode-extension/src/controller.ts apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/src/extension.ts apps/vscode-extension/test/composer-models.test.ts
git commit -m "feat(extension): composer model hot-swap host path (keyed providers via SecretStorage)"
```

---

### Task 14: ModelMenu + composer integration

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/ModelMenu.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (ModelOption mirror)
- Modify: `apps/vscode-extension/webview-ui/src/components/InputArea.tsx` (mount ModelMenu + gear)
- Test: `apps/vscode-extension/webview-ui/src/components/ModelMenu.test.tsx`

**Interfaces:**
- Consumes: host messages from Task 13 (`listModels`/`setModel`/`openSettings`, `modelList`/`modelSwapError`).
- Produces: `ModelMenu()` self-contained component; `types.ts` gains `export interface ModelOption { backend: string; label: string; model: string; active: boolean }`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/components/ModelMenu.test.tsx
import { render, screen, fireEvent, act } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { ModelMenu } from "./ModelMenu";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const LIST = {
  type: "modelList",
  current: { backend: "gemini", model: "gemini-flash-latest" },
  options: [
    { backend: "gemini", label: "Google Gemini", model: "gemini-flash-latest", active: true },
    { backend: "anthropic", label: "Anthropic", model: "claude-3-5-sonnet-latest", active: false },
  ],
};

function deliver(data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data }));
  });
}

describe("ModelMenu", () => {
  beforeEach(() => vi.clearAllMocks());

  it("requests the list on mount and shows the current model on the chip", () => {
    render(<ModelMenu />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "listModels" });
    deliver(LIST);
    expect(screen.getByRole("button", { name: /gemini-flash-latest/ })).toBeTruthy();
  });

  it("opens the popover and posts setModel for a non-active option", () => {
    render(<ModelMenu />);
    deliver(LIST);
    fireEvent.click(screen.getByRole("button", { name: /gemini-flash-latest/ }));
    fireEvent.click(screen.getByRole("button", { name: /Anthropic/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "setModel", backend: "anthropic", model: "claude-3-5-sonnet-latest",
    });
    // Popover closes when the updated list lands
    deliver({ ...LIST, current: { backend: "anthropic", model: "claude-3-5-sonnet-latest" } });
    expect(screen.queryByText("Google Gemini")).toBeNull();
  });

  it("renders a swap error inside the open popover", () => {
    render(<ModelMenu />);
    deliver(LIST);
    fireEvent.click(screen.getByRole("button", { name: /gemini-flash-latest/ }));
    fireEvent.click(screen.getByRole("button", { name: /Anthropic/ }));
    deliver({ type: "modelSwapError", message: "validation failed: 401" });
    expect(screen.getByText(/validation failed: 401/)).toBeTruthy();
  });

  it("gear-equivalent footer action posts openSettings", () => {
    render(<ModelMenu />);
    deliver(LIST);
    fireEvent.click(screen.getByRole("button", { name: /gemini-flash-latest/ }));
    fireEvent.click(screen.getByRole("button", { name: /Provider settings/ }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openSettings" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/components/ModelMenu.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement ModelMenu**

Add to `webview-ui/src/types.ts`:

```ts
/** Mirror of src/composer-models.ts ModelOption (webview never imports src/). */
export interface ModelOption {
  backend: string;
  label: string;
  model: string;
  active: boolean;
}
```

```tsx
// apps/vscode-extension/webview-ui/src/components/ModelMenu.tsx
import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { ModelOption } from "../types";
import { vscode } from "../vscodeApi";

function shortModel(model: string): string {
  return model.length > 18 ? `${model.slice(0, 17)}…` : model;
}

/**
 * ModelMenu — composer model chip + upward popover. Lists only providers the
 * host reports as swappable (stored key, or currently active). Selecting one
 * posts setModel; the popover stays open with a row spinner until the host
 * replies with the refreshed modelList (success → close) or modelSwapError
 * (error line, stays open). Swaps apply from the next turn (hot-swap route).
 */
export function ModelMenu() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<{ backend: string; model: string } | null>(null);
  const [options, setOptions] = useState<ModelOption[]>([]);
  const [swapping, setSwapping] = useState<string | null>(null); // backend in flight
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const swappingRef = useRef<string | null>(null);
  swappingRef.current = swapping;

  useEffect(() => {
    vscode.postMessage({ type: "listModels" });
    function onMessage(e: MessageEvent) {
      const m = e.data as Record<string, unknown>;
      if (m?.["type"] === "modelList") {
        setCurrent((m["current"] as { backend: string; model: string } | null) ?? null);
        setOptions((m["options"] as ModelOption[]) ?? []);
        setError(null);
        if (swappingRef.current !== null) {
          setSwapping(null);
          setOpen(false); // successful swap → close
        }
      } else if (m?.["type"] === "modelSwapError") {
        setSwapping(null);
        setError(m["message"] as string);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Close on outside click / Escape while open.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function choose(opt: ModelOption) {
    if (opt.active || swapping) return;
    setSwapping(opt.backend);
    setError(null);
    vscode.postMessage({ type: "setModel", backend: opt.backend, model: opt.model });
  }

  return (
    <div ref={rootRef} className="relative">
      {/* Chip */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={current ? `${current.backend} / ${current.model}` : "Select model"}
        className="flex h-6 items-center gap-1 rounded-[7px] border px-1.5 text-[10px] cursor-pointer transition-colors duration-150 hover:text-text"
        style={{
          background: "var(--color-surface-2)",
          borderColor: "var(--color-border-strong)",
          color: "var(--color-text-2)",
        }}
      >
        <span style={{ color: "var(--color-accent)" }}><Icon name="spark" size={9} /></span>
        <span className="max-w-[130px] truncate font-mono" style={{ fontSize: "9.5px" }}>
          {current ? shortModel(current.model) : "model"}
        </span>
        <Icon name="chev-d" size={8} />
      </button>

      {/* Popover (above the composer) */}
      {open && (
        <div
          role="menu"
          className="anim-rise absolute bottom-full left-0 z-50 mb-1.5 w-[240px] rounded-[10px] border p-1"
          style={{
            background: "var(--color-surface-2)",
            borderColor: "var(--color-border-strong)",
            boxShadow: "0 10px 30px -10px rgba(0,0,0,.7), inset 0 1px 0 var(--hairline)",
          }}
        >
          {options.map((opt) => (
            <button
              key={opt.backend}
              type="button"
              role="menuitem"
              onClick={() => choose(opt)}
              className="flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-md border-0 bg-transparent px-2 py-1.5 text-left transition-colors duration-150 hover:bg-[var(--accent-bg)]"
            >
              <span className="flex w-full items-center gap-1.5">
                <span className="flex-1 text-[9.5px] font-semibold uppercase tracking-wide text-text-3">
                  {opt.label}
                </span>
                {opt.active && <span style={{ color: "var(--color-accent)" }}><Icon name="check" size={10} /></span>}
                {swapping === opt.backend && (
                  <span
                    className="inline-block rounded-full border-2"
                    style={{
                      width: 9, height: 9,
                      borderColor: "var(--color-accent-ink) var(--accent-bg) var(--accent-bg) var(--accent-bg)",
                      animation: "spin 0.75s linear infinite",
                    }}
                  />
                )}
              </span>
              <span className="font-mono text-[10.5px] text-text">{opt.model}</span>
            </button>
          ))}
          {options.length === 0 && (
            <p className="px-2 py-2 text-[10.5px] text-text-3">
              No validated providers yet — add one in Settings.
            </p>
          )}
          {error && (
            <p className="px-2 py-1.5 text-[10.5px]" style={{ color: "var(--color-red)" }}>{error}</p>
          )}
          <div className="mt-1 border-t pt-1" style={{ borderColor: "var(--hairline)" }}>
            <button
              type="button"
              role="menuitem"
              onClick={() => { vscode.postMessage({ type: "openSettings" }); setOpen(false); }}
              className="flex w-full cursor-pointer items-center gap-1.5 rounded-md border-0 bg-transparent px-2 py-1.5 text-left text-[10.5px] text-text-2 transition-colors duration-150 hover:bg-surface-3 hover:text-text"
            >
              <Icon name="gear" size={10} /> Provider settings…
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Mount in InputArea**

In `InputArea.tsx`, import `ModelMenu` and add at the START of the footer row (before the `availability.taskStop` block):

```tsx
      {/* Footer row */}
      <div className="flex items-center gap-1.5 pt-1">
        {/* Model hot-swap + settings shortcut */}
        <ModelMenu />
        <button
          type="button"
          onClick={() => vscode.postMessage({ type: "openSettings" })}
          aria-label="Open settings"
          title="AI Editor settings"
          className="flex h-6 w-6 items-center justify-center rounded-[7px] cursor-pointer text-text-3 transition-colors duration-150 hover:bg-surface-2 hover:text-text"
        >
          <Icon name="gear" size={12} />
        </button>
```

Extend `InputArea.test.tsx` with one assertion (new `it` block in the existing describe):

```tsx
  it("renders the model chip and the settings gear", () => {
    render(<Harness />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "listModels" });
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openSettings" });
  });
```

- [ ] **Step 5: Run tests + typecheck**

Run: `cd "apps/vscode-extension/webview-ui" && npx vitest run src/components/ModelMenu.test.tsx src/components/InputArea.test.tsx && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/ModelMenu.tsx apps/vscode-extension/webview-ui/src/types.ts apps/vscode-extension/webview-ui/src/components/InputArea.tsx apps/vscode-extension/webview-ui/src/components/InputArea.test.tsx apps/vscode-extension/webview-ui/src/components/ModelMenu.test.tsx
git commit -m "feat(webview): composer model-swap popover and settings gear"
```

---

### Task 15: Full verification + live smoke

- [ ] **Step 1: Full builds and suites**

```bash
npm run build                      # all TS workspaces (also rebuilds webview-ui dist)
npm run typecheck
npm run -w crucible-vscode-extension test
cd "apps/vscode-extension/webview-ui" && npx vitest run
```
Expected: everything green. (Reminder: `webview-ui/dist` MUST be rebuilt or the dev host serves the old UI.)

- [ ] **Step 2: Live smoke (dev host)** — start the backend per CLAUDE.md, then:

```bash
code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/shadow-forge-stress"
```

Checklist:
1. `crucible.openSettingsPanel` → lands on Overview; cards stagger in; hover lifts.
2. Click through all 7 nav items — indicator slides, content pane animates, no console errors.
3. Provider: save & validate → "✓ Saved" chip; Active line updates.
4. MCP: status dots correct; toggle a server (switch animates, reconnect fires); add + remove a dummy server.
5. Skills: search filters; toggling flips the restart banner in (slide-down).
6. Instructions: empty state → Create → type → Save → send a chat message and confirm the backend picked the rules up (no restart).
7. Runtime: versions render; Restart backend works.
8. Setup wizard (`crucible.runSetup`): StepRail advances, install rows animate, done step reachable.
9. Composer: chip shows current model; swap to another keyed provider; next chat turn uses it (check `.crucible/state` log for the new model); swap error (bogus key) renders in-popover; gear opens Settings.
10. Toggle macOS "Reduce motion" and confirm the UI is instant but functional.

- [ ] **Step 3: Commit any smoke fixes, then final commit**

```bash
git add -A && git commit -m "chore(ui): post-smoke polish for settings/setup/composer overhaul"
```

---

## Self-Review (done at plan time)

- **Spec coverage:** §3 left-nav + 7 sections → Tasks 3–8, 10, 11. §4 Instructions → Tasks 9–10. §5 Setup reskin → Task 12. §6 composer quick-bar → Tasks 13–14. §8 unit+smoke → every task + Task 15. §9 risks: section-splitting churn handled (no existing webview settings tests to migrate; extension-side `settings-data.test.ts` extended not moved); model-dropdown reuses `PROVIDERS` (risk 3) via `setup-data.ts` on the host and `settings/types.ts` in the webview.
- **Placeholder scan:** none — every step carries code or an exact command.
- **Type consistency:** `SectionId`/`SectionProps` defined once in `sections/meta.ts`; `ModelOption` defined in `src/composer-models.ts` and mirrored (with a mirror-comment) in `webview-ui/src/types.ts`; instructions messages added to both `src/settings-data.ts` and `webview-ui/src/settings/types.ts`; `Step` moves to `StepRail.tsx`.
