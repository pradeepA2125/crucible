/** Physical occupancy is shared across projects and released only by actual settlement. */
let pendingFiles = 0;
export class WorkspaceReadBusy extends Error {}
export async function admittedFileRead<T>(work: () => Promise<T>): Promise<T> {
  if (pendingFiles >= 4) throw new WorkspaceReadBusy("Earlier file reads are still finishing.");
  pendingFiles++;
  try { return await work(); } finally { pendingFiles--; }
}
