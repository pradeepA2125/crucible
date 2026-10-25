import { afterEach, expect, it } from "vitest";
import { mkdtemp, realpath, writeFile, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { rasterMediaType, rasterDimensions, MAX_RASTER_BYTES } from "../src/workspace-raster.js";
import { createWorkspaceFiles } from "../src/workspace-files.js";
const roots: string[] = [];
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
it.each([
  [[137,80,78,71,13,10,26,10], "image/png"], [[255,216,255], "image/jpeg"],
  [Array.from(Buffer.from("GIF89a")), "image/gif"], [Array.from(Buffer.from("BM000000000000")), "image/bmp"],
  [Array.from(Buffer.from("RIFF0000WEBPVP8 ")), "image/webp"],
])("uses image bytes rather than an extension", (bytes, type) => { expect(rasterMediaType(Buffer.from(bytes))).toBe(type); });
it("refuses SVG/HTML, oversize and links; returns only bounded local image data", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "crucible-raster-"))); roots.push(root);
  const files = await createWorkspaceFiles(root);
  await writeFile(join(root, "fake.png"), "<svg><script>alert(1)</script></svg>");
  await expect(files.image("fake.png")).rejects.toMatchObject({ reason: "unsupported" });
  await writeFile(join(root, "large.png"), Buffer.alloc(MAX_RASTER_BYTES + 1));
  await expect(files.image("large.png")).rejects.toMatchObject({ reason: "unsupported", message: expect.stringContaining("8 MiB") });
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y3i8AAAAASUVORK5CYII=", "base64");
  await writeFile(join(root, "picture.data"), png); await symlink("picture.data", join(root, "link.png"));
  expect(await files.image("picture.data")).toMatchObject({ path: "picture.data", mediaType: "image/png", size: png.length, dataUrl: `data:image/png;base64,${png.toString("base64")}` });
  await expect(files.image("link.png")).rejects.toMatchObject({ reason: "symlink" });
  await expect(files.image(".git/config")).rejects.toMatchObject({ reason: "protected" });
});

it("refuses excessive or truncated dimension headers before handing bytes to Chromium", () => {
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y3i8AAAAASUVORK5CYII=", "base64");
  expect(rasterDimensions(png)).toEqual({ width: 1, height: 1 });
  const bomb = Buffer.from(png); bomb.writeUInt32BE(20000, 16); bomb.writeUInt32BE(20000, 20);
  expect(rasterDimensions(bomb)).toBeUndefined();
  expect(rasterDimensions(png.subarray(0, 18))).toBeUndefined();
});
