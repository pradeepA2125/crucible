# Crucible — landing page

Marketing/landing site for the Crucible open-source AI code editor.
Standalone Vite app, deliberately **outside** the npm workspace (`apps/*`) so it
never affects the monorepo build.

## Stack

- React 19 + TypeScript + Vite
- Tailwind CSS v4 (`@tailwindcss/vite`, `@theme` design tokens)
- Framer Motion (scroll reveals, palette, transitions)
- Three.js via React Three Fiber + drei (hero "dependency space")

## Design direction — "VOID / CRUCIBLE"

The hero renders the workspace as a **dependency space** — code as a galaxy:
package clusters as nebulae, an
entry-point beacon, pulses riding call/import edges, and a ghosted **shadow twin**
of the whole graph (the shadow workspace) drifting behind it. The HUD is functional,
not decorative:

- **Edge layers** — Imports / Calls / Inherits toggles actually toggle the 3D layers
- **Theme** — 01 Void Violet · 02 Abyss Cyan · 03 Ember Dusk (Axon's palettes) repaint
  the entire page + scene via CSS variables, live
- **⌘K** — a real command palette: fly to sections, repaint the space, GitHub, copy install
- **Ride the beam** — the left rail is scroll progress as a traveling pulse

Type system: Space Grotesk (structure) + Instrument Serif italic (editorial accents) +
JetBrains Mono (code/HUD). Copy is grounded in the actual product (CLAUDE.md), and is
community/GitHub-focused — no SaaS language.

## Commands

```bash
npm install
npm run dev        # dev server
npm run build      # typecheck + production build → dist/
npm run preview    # serve dist/
```
