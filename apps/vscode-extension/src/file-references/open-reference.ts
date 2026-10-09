import type { CatalogSnapshot, RetainedFile } from "./catalog.js";
import { buildReferenceIndex, matchReference, parseReference } from "./matching.js";

export interface OpenReferenceDeps<U> {
  refresh(): Promise<CatalogSnapshot<U>>;
  identity(uri: U): string;
  isCurrentFile(file: RetainedFile<U>): Promise<boolean>;
  pick(files: readonly RetainedFile<U>[]): Promise<RetainedFile<U> | undefined>;
  open(uri: U, location?: { line: number; endLine: number }): Promise<void>;
  notice(message: string): void;
}

const ordinal = (a: string, b: string): number => a < b ? -1 : a > b ? 1 : 0;

function candidates<U>(snapshot: CatalogSnapshot<U>, reference: string): RetainedFile<U>[] {
  const index = buildReferenceIndex(snapshot.files.map(file => file.entry));
  const matched = new Set(matchReference(index, reference));
  return snapshot.files.filter(file => matched.has(file.entry)).sort((a, b) =>
    ordinal(a.entry.workspaceLabel, b.entry.workspaceLabel)
    || ordinal(a.entry.relativePath, b.entry.relativePath)
    || ordinal(a.entry.workspaceId, b.entry.workspaceId));
}

export async function openFileReference<U>(value: unknown, deps: OpenReferenceDeps<U>): Promise<void> {
  const reference = parseReference(value);
  if (!reference || typeof value !== "string") {
    deps.notice("Cannot open an invalid file reference.");
    return;
  }
  const notFound = () => deps.notice(`Workspace file not found: ${value}`);
  try {
    const files = candidates(await deps.refresh(), value);
    if (!files.length) { notFound(); return; }
    let current = files[0]!;
    if (files.length > 1) {
      const selected = await deps.pick(files);
      if (!selected) return;
      const selectedId = deps.identity(selected.uri);
      // A picker must return one of the offered candidates, not an arbitrary URI.
      if (!files.some(file => deps.identity(file.uri) === selectedId
        && file.entry.workspaceId === selected.entry.workspaceId
        && file.entry.relativePath === selected.entry.relativePath)) {
        notFound();
        return;
      }
      const latest = candidates(await deps.refresh(), value).find(file =>
        deps.identity(file.uri) === selectedId
        && file.entry.workspaceId === selected.entry.workspaceId
        && file.entry.relativePath === selected.entry.relativePath);
      if (!latest) { notFound(); return; }
      current = latest;
    }
    if (!(await deps.isCurrentFile(current))) { notFound(); return; }
    if (reference.line) {
      await deps.open(current.uri, { line: reference.line, endLine: reference.endLine! });
    } else {
      await deps.open(current.uri);
    }
  } catch {
    deps.notice(`Unable to open workspace file: ${value}`);
  }
}
