// Bundles src/extension.ts + everything it imports (including the workspace
// dependency @crucible/editor-client and its own dependency zod) into a single
// self-contained dist/extension.js. This exists so `vsce package --no-dependencies`
// (required in this npm-workspaces monorepo — vsce's default dependency-bundling
// crashes trying to zip files that sit at the monorepo root, see
// docs/superpowers/plans/2026-07-02-p4-install-runtime-settings.md Task 17) never
// needs to find node_modules content at packaging time: it's already inlined here.
import { rmSync } from "node:fs";
import { build } from "esbuild";

// Clear stale per-file output from when "build" used to be a plain `tsc` emit
// (one dist/<name>.js per src/<name>.ts) — bundling supersedes that entirely,
// and leftover files would otherwise get zipped into the VSIX as dead weight.
rmSync("dist", { recursive: true, force: true });

await build({
  entryPoints: ["src/extension.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  target: "es2022",
  outfile: "dist/extension.js",
  external: ["vscode"],
  sourcemap: true,
  logLevel: "info",
});
