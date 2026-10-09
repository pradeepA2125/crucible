export interface FileReferenceEntry {
  workspaceId: string;
  workspaceLabel: string;
  relativePath: string;
}
export interface FileReferenceIndex {
  byPath: ReadonlyMap<string, readonly FileReferenceEntry[]>;
  byBasename: ReadonlyMap<string, readonly FileReferenceEntry[]>;
}
export interface Reference { path: string; hasDirectory: boolean; line?: number; endLine?: number; }

/** Exact workspace paths with an optional positive :line or :start-end citation. */
export function parseReference(value: unknown): Reference | null {
  if (typeof value !== "string" || !value || value.length > 4096) return null;
  let source = value;
  const location = source.match(/:(\d+)(?:[-–](\d+))?$/u);
  let line: number | undefined;
  let endLine: number | undefined;
  if (location) {
    line = Number(location[1]);
    endLine = location[2] ? Number(location[2]) : line;
    if (!Number.isSafeInteger(line) || line < 1 || !Number.isSafeInteger(endLine) || endLine < line) return null;
    source = source.slice(0, -location[0].length);
  }
  if (/[\s\u0000-\u001f\u007f\\:%?#]/u.test(source)) return null;
  const path = source.startsWith("./") ? source.slice(2) : source;
  const parts = path.split("/");
  if (parts.some(part => !part || part === "." || part === "..")) return null;
  const name = parts[parts.length - 1];
  if (!name || !/^.+\.[^./]+$/u.test(name)) return null;
  return { path, hasDirectory: parts.length > 1, ...(line ? { line, endLine: endLine! } : {}) };
}

export function buildReferenceIndex(entries: readonly FileReferenceEntry[]): FileReferenceIndex {
  const byPath = new Map<string, FileReferenceEntry[]>();
  const byBasename = new Map<string, FileReferenceEntry[]>();
  for (const entry of entries) {
    const ref = parseReference(entry.relativePath);
    if (!ref) continue;
    const parts = ref.path.split("/");
    const basename = parts[parts.length - 1]!;
    for (const [map, key] of [[byPath, ref.path], [byBasename, basename]] as const) {
      const bucket = map.get(key) ?? [];
      bucket.push(entry);
      map.set(key, bucket);
    }
  }
  return { byPath, byBasename };
}

export function matchReference(index: FileReferenceIndex, value: unknown): readonly FileReferenceEntry[] {
  const ref = parseReference(value);
  return ref ? (ref.hasDirectory ? index.byPath : index.byBasename).get(ref.path) ?? [] : [];
}
