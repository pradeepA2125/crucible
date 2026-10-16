# Crucible — landing page

Standalone React/TypeScript/Vite site, outside the `apps/*` workspace. Cloudflare Pages builds this directory with `npm run build` and serves `dist/` at https://crucible-ai.pages.dev/.

## Production source

`src/experience/` contains the approved product page: the dependency-space hero, illustrated background agents, team room, rewind checkpoints, provider choices, context, and the interactive Axon demonstration. `src/App.tsx` exports this page. The rejected sculpture design is no longer connected to the production entry.

The historical `.impeccable/prototype/` URL imports the same App. Both HTML entries are built, so local previews and production use the same implementation and assets rather than diverging designs.

## Assets and motion

Fonts are served locally. The hero graph is illustrative; the Axon section lazy-loads the actual product renderer with a captured, sanitized repository subset. Its poster and recording are renderer demonstrations, not recordings of the editor UI. Graph metadata excludes source bodies, absolute paths, settings and memory data.

Native scrolling drives the illustrations. Pause and reduced-motion controls retain static content. The interactive Axon scene activates explicitly and disposes while offscreen, hidden or paused. Installation, source and documentation use ordinary links; illustrative feature controls do not change a real workspace.

## Local preview

Use Node.js 22.12+ or the configured newer runtime:

```bash
cd landing
npm ci
npm run build
npm run preview -- --host 127.0.0.1 --port 4175 --strictPort
```

Open http://127.0.0.1:4175/. The prior http://127.0.0.1:4175/.impeccable/prototype/ URL serves the same page. Stop the preview with Ctrl-C. For development, run `npm run dev -- --host 127.0.0.1`.

## Deployment and validation

Cloudflare Pages deploys the `landing/` build from the repository's production branch. GitHub's tagged extension release workflow is separate and does not promote a prototype into this page.

The build typechecks the shared Axon modules and packages both entry points. TypeScript constructor parameter properties are enabled for the shared renderer. Its lazy WebGL chunk can exceed Vite's 500 kB warning threshold; it is requested only when the visitor activates 3D.

Promotion checks covered both built URLs, 1440/390/320 px layouts without horizontal overflow, agent dispatch and team controls, rewind preview/restore, pause/resume, reduced motion with no running animations, local anchors, and Axon file tracing, symbols and recording playback. No page JavaScript errors were observed. These checks are not a cross-browser or physical-device GPU certification.
