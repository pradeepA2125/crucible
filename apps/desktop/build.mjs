import { build } from "esbuild";
import { cp, mkdir, readFile, writeFile, chmod } from "node:fs/promises";
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { dirname } from "node:path";
import { nodePtyPayload } from "./node-pty-payload.mjs";
import { resolve } from "node:path";
const base = import.meta.dirname;
const dist = resolve(base, "dist");
await mkdir(dist, { recursive: true });
// node-pty uses stable Node-API binaries. Keep native files outside bundles (the published
// prebuild, or the module npm compiled on Linux) and restore the npm archive's missing
// executable bit on the Unix spawn helper.
const nativeRoot=dirname(createRequire(import.meta.url).resolve("node-pty/package.json"));
const nativeTarget=resolve(dist,"node_modules/node-pty");
await mkdir(nativeTarget,{recursive:true});
const pty=nodePtyPayload(process.platform,process.arch,path=>existsSync(resolve(nativeRoot,path)));
for(const entry of pty.entries)await cp(resolve(nativeRoot,entry),resolve(nativeTarget,entry),{recursive:true});
if(pty.helper)await chmod(resolve(nativeTarget,pty.helper),0o755);

await Promise.all([build({ entryPoints: [resolve(base, "src/main.ts")], bundle: true, platform: "node", target: "node22", format: "cjs", external: ["electron", "node-pty"], outfile: resolve(dist, "main.cjs") }),
  build({ entryPoints: [resolve(base, "src/preload.ts")], bundle: true, platform: "browser", target: "chrome130", format: "cjs", external: ["electron"], outfile: resolve(dist, "preload.cjs") })]);
await build({entryPoints:[resolve(base,"src/terminal-worker.ts")],bundle:true,platform:"node",target:"node22",format:"cjs",external:["node-pty"],outfile:resolve(dist,"terminal-worker.cjs")});
await build({entryPoints:[resolve(base,"src/update-worker.ts")],bundle:true,platform:"node",target:"node22",format:"cjs",outfile:resolve(dist,"update-worker.cjs")});
// Spoken replies off Apple Silicon: sherpa-onnx's Node addon stays outside the bundle, beside
// its prebuilt platform package (sherpa-onnx-<win|linux|darwin>-<arch>), which it loads as a sibling.
await build({entryPoints:[resolve(base,"src/voice/pocket-worker.ts")],bundle:true,platform:"node",target:"node22",format:"cjs",external:["sherpa-onnx-node"],outfile:resolve(dist,"voice-pocket-worker.cjs")});
const sherpaPlatform=`sherpa-onnx-${process.platform==="win32"?"win":process.platform}-${process.arch}`;
for(const name of ["sherpa-onnx-node",sherpaPlatform]){
  const root=dirname(createRequire(import.meta.url).resolve(`${name}/package.json`));
  await cp(root,resolve(dist,"node_modules",name),{recursive:true,dereference:true});
}
await cp(resolve(base, "../vscode-extension/webview-ui/dist"), resolve(dist, "renderer"), { recursive: true });
await build({
  entryPoints: [resolve(base, "../vscode-extension/webview-ui/src/desktop/voice/capture-worklet.ts")],
  bundle: true, platform: "browser", target: "chrome130", format: "esm",
  outfile: resolve(dist, "renderer/voice-capture.js"),
});

await cp(resolve(base, "../vscode-extension/resources"), resolve(dist, "resources"), { recursive: true });
const desktopRequire=createRequire(import.meta.url),yauzlRoot=dirname(desktopRequire.resolve("yauzl/package.json")),yauzlRequire=createRequire(resolve(yauzlRoot,"package.json"));
await mkdir(resolve(dist,"resources/licenses"),{recursive:true});
for(const [name,folder] of [["yauzl",yauzlRoot],["pend",dirname(yauzlRequire.resolve("pend/package.json"))]]){await cp(resolve(folder,"LICENSE"),resolve(dist,`resources/licenses/${name}.txt`));}
const csp = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
for (const name of ["index", "setup", "settings", "desktop", "desktop-app", "memory", "graph", "lifecycle"]) {
  const path = resolve(dist, `renderer/${name}.html`);
  const html = await readFile(path, "utf8");
  await writeFile(path, html.replace("<head>", `<head><meta http-equiv="Content-Security-Policy" content="${["graph", "desktop-app"].includes(name) ? csp.replace("worker-src 'none'", "worker-src 'self'") : csp}">`));
}
