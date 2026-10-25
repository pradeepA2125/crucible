import { build } from "esbuild";
import { cp, mkdir, readFile, writeFile, chmod } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname } from "node:path";
import { resolve } from "node:path";
const base = import.meta.dirname;
const dist = resolve(base, "dist");
await mkdir(dist, { recursive: true });
// node-pty uses stable Node-API binaries. Keep native files outside bundles and
// restore the npm archive's missing executable bit on the packaged Unix helper.
const nativeRoot=dirname(createRequire(import.meta.url).resolve("node-pty/package.json"));
const nativeTarget=resolve(dist,"node_modules/node-pty");
await mkdir(nativeTarget,{recursive:true});
for(const entry of ["lib","prebuilds","package.json","LICENSE"])await cp(resolve(nativeRoot,entry),resolve(nativeTarget,entry),{recursive:true});
if(process.platform!=="win32")await chmod(resolve(nativeTarget,`prebuilds/${process.platform}-${process.arch}/spawn-helper`),0o755);

await Promise.all([build({ entryPoints: [resolve(base, "src/main.ts")], bundle: true, platform: "node", target: "node22", format: "cjs", external: ["electron", "node-pty"], outfile: resolve(dist, "main.cjs") }),
  build({ entryPoints: [resolve(base, "src/preload.ts")], bundle: true, platform: "browser", target: "chrome130", format: "cjs", external: ["electron"], outfile: resolve(dist, "preload.cjs") })]);
await build({entryPoints:[resolve(base,"src/terminal-worker.ts")],bundle:true,platform:"node",target:"node22",format:"cjs",external:["node-pty"],outfile:resolve(dist,"terminal-worker.cjs")});
await build({entryPoints:[resolve(base,"src/update-worker.ts")],bundle:true,platform:"node",target:"node22",format:"cjs",outfile:resolve(dist,"update-worker.cjs")});
await cp(resolve(base, "../vscode-extension/webview-ui/dist"), resolve(dist, "renderer"), { recursive: true });
await cp(resolve(base, "../vscode-extension/resources"), resolve(dist, "resources"), { recursive: true });
const desktopRequire=createRequire(import.meta.url),yauzlRoot=dirname(desktopRequire.resolve("yauzl/package.json")),yauzlRequire=createRequire(resolve(yauzlRoot,"package.json"));
await mkdir(resolve(dist,"resources/licenses"),{recursive:true});
for(const [name,folder] of [["yauzl",yauzlRoot],["pend",dirname(yauzlRequire.resolve("pend/package.json"))]]){await cp(resolve(folder,"LICENSE"),resolve(dist,`resources/licenses/${name}.txt`));}
const csp = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
for (const name of ["index", "setup", "settings", "desktop", "desktop-app", "memory", "graph", "lifecycle"]) {
  const path = resolve(dist, `renderer/${name}.html`);
  const html = await readFile(path, "utf8");
  await writeFile(path, html.replace("<head>", `<head><meta http-equiv="Content-Security-Policy" content="${name === "graph" ? csp.replace("worker-src 'none'", "worker-src 'self'") : csp}">`));
}
