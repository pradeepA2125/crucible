// vscode-free: real archive extraction backing InstallerDeps.extract in
// production (tests inject a fake — see installer.ts). tar.gz is extracted
// straight off the in-memory buffer via the `tar` package's streaming API
// (`Readable.from(buffer).pipe(tar.x(...))`, gzip auto-detected). .zip has
// no such buffer/stream API in `extract-zip` — it only accepts a file path —
// so it's staged to a throwaway temp file first.
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import extractZip from "extract-zip";
import * as tar from "tar";
import type { ArchiveFormat } from "./installer.js";

export async function extractArchive(
  archive: Buffer,
  destDir: string,
  format: ArchiveFormat,
): Promise<void> {
  if (format === "tar.gz") {
    await pipeline(Readable.from(archive), tar.x({ cwd: destDir }));
    return;
  }

  const stagingDir = await mkdtemp(join(tmpdir(), "crucible-zip-"));
  try {
    const zipPath = join(stagingDir, "archive.zip");
    await writeFile(zipPath, archive);
    await extractZip(zipPath, { dir: destDir });
  } finally {
    await rm(stagingDir, { recursive: true, force: true });
  }
}
