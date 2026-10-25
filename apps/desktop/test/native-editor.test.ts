import { afterEach, expect, it } from "vitest";
import { mkdtemp, mkdir, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { editorEnvironment, validateEditor } from "../src/native-editor.js";
const roots: string[] = [];
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
it("rejects repository executables and canonical aliases, revalidates selected apps", async () => {
  const base = await realpath(await mkdtemp(join(tmpdir(), "crucible-editor-"))); roots.push(base);
  const root = join(base, "project"), app = join(base, "Editor.app"); await mkdir(root); await mkdir(app);
  await writeFile(join(root, "editor.sh"), "document fixture"); await symlink(join(root, "editor.sh"), join(base, "alias"));
  await expect(validateEditor(root, join(root, "editor.sh"), "darwin")).rejects.toThrow("outside this workspace");
  await expect(validateEditor(root, join(base, "alias"), "darwin")).rejects.toThrow("outside this workspace");
  expect(await validateEditor(root, app, "darwin")).toBe(app);
  await expect(validateEditor(root, app, "linux")).rejects.toThrow("Choose an editor");
  await rm(app, { recursive: true }); await expect(validateEditor(root, app, "darwin")).rejects.toThrow();
});
it("keeps only explicitly allowed operating-system environment fields", () => {
  const source = { PATH: "/usr/bin", HOME: "/home/user", CRUCIBLE_DESKTOP_PROFILE: "/private/profile", ELECTRON_RUN_AS_NODE: "1", NODE_OPTIONS: "--inspect", PYTHONPATH: "/internal" };
  expect(editorEnvironment(source)).toEqual({ PATH: "/usr/bin", HOME: "/home/user" });
});
