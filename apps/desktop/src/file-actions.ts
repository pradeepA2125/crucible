/** A transcript link reveals a file; it must never run the file's default handler. */
export function createFileRevealer(shell: { showItemInFolder(path: string): void }) {
  return async (path: string): Promise<void> => { shell.showItemInFolder(path); };
}
