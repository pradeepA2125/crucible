# Crucible — landing page

Standalone React/TypeScript/Vite site, outside the `apps/*` workspace.

## Direction: the shape of an edit

An original procedural Three.js sculpture accompanies an editorial scroll story:
understand the repository, collaborate, then move accepted patches from shadow
staging into the working tree. Three interwoven tubular forms separate and rotate
with scroll, then converge. This is a product metaphor, not a live repository graph.
No third-party template code, models, textures or video were copied.

The existing Space Grotesk / Instrument Serif / JetBrains Mono typography and
violet, cyan and ember accents are retained. A light/dark toggle starts from the
system preference. Product copy follows the root README, including the experimental
Claude subscription path and the distinction between patch staging and sandboxing.

## Motion and fallback

- Lazy-loaded React Three Fiber scene; demand rendering (no idle animation loop).
- Three procedural meshes, capped 1.5 device pixel ratio, no shadows or postprocessing.
- Scroll updates transform the sculpture; the footer can pause/resume updates.
- Reduced motion skips the Canvas entirely and uses a static CSS sculpture.
- Missing WebGL2, context loss or a scene error retains the static fallback.
- Decorative geometry is hidden from assistive technology; all content is HTML.
- Skip link, visible keyboard focus, native links/select/buttons and narrow layouts.

## Local preview (existing dependencies)

```bash
cd landing
npm run lint
npm run build
npm run preview -- --host 127.0.0.1 --port 4173 --strictPort
```

Open http://127.0.0.1:4173/ and scroll through the first four chapters. Stop with
Ctrl-C. For development, use `npm run dev -- --host 127.0.0.1`.

## Verification notes

Production build and lint pass. Lint reports an existing Fast Refresh export warning
in `src/theme.tsx`; Vite reports a large lazy Three.js chunk (~903 kB / 243 kB gzip).
Browser smoke checks covered 1440, 390 and 320 px widths without horizontal overflow,
theme switching, scroll-driven pixel changes, pause/resume labeling, keyboard skip
navigation, reduced-motion Canvas removal and simulated unavailable WebGL fallback.
This is not a full screen-reader audit, cross-browser matrix or physical-device GPU
benchmark. Fonts retain the existing Google Fonts request and system fallbacks.