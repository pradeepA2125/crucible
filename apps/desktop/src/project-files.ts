import { execFile } from "node:child_process";
import { stat } from "node:fs/promises";
import { readFileSync, realpathSync, writeFileSync, renameSync, lstatSync, rmSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { basename, join, relative, isAbsolute, sep } from "node:path";
import { binPath } from "../../vscode-extension/src/runtime/installer.js";
import { FileReferenceCatalog } from "../../vscode-extension/src/file-references/catalog.js";
import { createFileReferenceBridge } from "../../vscode-extension/src/file-references/bridge.js";
import { openFileReference } from "../../vscode-extension/src/file-references/open-reference.js";
import { containedPath } from "./security.js";

export function projectFiles(root: string, runtimeDir: string, open: (path: string) => Promise<void>, notice: (text: string) => void, post: (message: Record<string, unknown>) => void) {
  const inventory = async (): Promise<string[]> => {
    const paths = await new Promise<string[]>((resolve, reject) => {
      execFile(binPath(runtimeDir, "rg"), ["--files", "--hidden", "-g", "!.git", "-g", "!.crucible", "-g", "!node_modules", "-g", "!.venv", "-g", "!dist", "-g", "!target"], { cwd: root, timeout: 10000, maxBuffer: 2 * 1024 * 1024 }, (error, stdout) => {
        if (error && (error as NodeJS.ErrnoException & { code?: number }).code !== 1) { reject(new Error("Project file inventory failed.")); return; }
        resolve(stdout.split(/\r?\n/).filter(Boolean).slice(0, 5000));
      });
    });
    const safe: string[] = [];
    for (const path of paths) { try { if ((await stat(await containedPath(root, path))).isFile()) safe.push(path); } catch { /* removed or outside root */ } }
    return safe;
  };
  const catalog = new FileReferenceCatalog<string>({
    enumerate: async () => Promise.all((await inventory()).map(async relativePath => ({ uri: await containedPath(root, relativePath), root,
      entry: { workspaceId: root, workspaceLabel: basename(root), relativePath } }))),
    onInvalidate: () => ({ dispose() {} }), reportFailure: () => notice("Project files could not be listed."),
  });
  const openProjectFile = async (path: string) => {
    const canonical = await containedPath(root, path);
    if (!(await stat(canonical)).isFile()) throw new Error("Choose a project file.");
    await open(canonical);
  };
  const bridge = createFileReferenceBridge(catalog, reference => openFileReference(reference, {
    refresh: () => catalog.refresh(), identity: path => path,
    isCurrentFile: async file => { try { return await containedPath(root, file.uri) === file.uri && (await stat(file.uri)).isFile(); } catch { return false; } },
    pick: async () => { notice("Use a full project path to choose between files with the same name."); return undefined; },
    open: openProjectFile, notice,
  }), message => post({ ...message }));
  return { inventory, open: openProjectFile, bridge,
    readInstructions: () => { try {
      const path = realpathSync(join(root, "AGENTS.md"));
      const within = relative(root, path);
      if (isAbsolute(within) || within === ".." || within.startsWith(`..${sep}`)) throw new Error("Outside project.");
      return { content: readFileSync(path, "utf8"), exists: true };
    } catch { return { content: "", exists: false }; } },
    writeInstructions: (content: string) => {
      const destination = join(root, "AGENTS.md");
      const temp = join(root, `.crucible-instructions-${randomUUID()}.tmp`);
      let mode = 0o644;
      try { const info = lstatSync(destination); if (info.isFile()) mode = info.mode & 0o777; } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
      // Exclusive temp creation + rename never follow a planted destination symlink.
      try { writeFileSync(temp, content, { flag: "wx", mode }); renameSync(temp, destination); }
      finally { rmSync(temp, { force: true }); }
    },
    dispose: () => { bridge.dispose(); catalog.dispose(); },
  };
}
