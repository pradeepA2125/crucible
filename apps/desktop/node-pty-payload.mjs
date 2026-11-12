// Which node-pty files the desktop build copies beside the bundles, for the platform it runs on.
// Shared by build.mjs and its tests.

/**
 * node-pty publishes prebuilds for macOS and Windows only; on Linux npm compiles the module into
 * build/Release instead (node-pty's own loader checks build/Release before prebuilds). `has`
 * answers whether a path relative to the node-pty package exists. The spawn helper, where one
 * exists, loses its executable bit in the npm archive and is restored by the caller.
 */
export function nodePtyPayload(platform, arch, has) {
  const prebuild = `prebuilds/${platform}-${arch}`;
  const native = has(prebuild) ? prebuild : has("build/Release") ? "build/Release" : null;
  if (!native) {
    throw new Error(`node-pty has no native module for ${platform}-${arch} (neither ${prebuild} nor build/Release). Run: npm rebuild node-pty`);
  }
  const helper = platform !== "win32" && has(`${native}/spawn-helper`) ? `${native}/spawn-helper` : null;
  return { entries: ["lib", "package.json", "LICENSE", native === prebuild ? "prebuilds" : native], helper };
}
