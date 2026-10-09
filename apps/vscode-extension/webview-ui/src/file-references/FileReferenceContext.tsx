import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { vscode } from "../vscodeApi";
import {
  buildReferenceIndex,
  type FileReferenceEntry,
  type FileReferenceIndex,
} from "../../../src/file-references/matching";

const EMPTY_INDEX: FileReferenceIndex = { byPath: new Map(), byBasename: new Map() };
const FileReferenceContext = createContext<FileReferenceIndex>(EMPTY_INDEX);

function isEntry(value: unknown): value is FileReferenceEntry {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const entry = value as Record<string, unknown>;
  return typeof entry.workspaceId === "string"
    && typeof entry.workspaceLabel === "string"
    && typeof entry.relativePath === "string";
}

export function FileReferenceProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<{ revision: number; entries: readonly FileReferenceEntry[] }>({
    revision: -1,
    entries: [],
  });

  useEffect(() => {
    const receive = (event: MessageEvent<unknown>) => {
      const message = event.data;
      if (!message || typeof message !== "object" || Array.isArray(message)) return;
      const envelope = message as Record<string, unknown>;
      if (envelope.type !== "fileReferenceCatalog" || !Number.isInteger(envelope.revision)
        || !Array.isArray(envelope.entries) || !envelope.entries.every(isEntry)) return;
      const revision = envelope.revision as number;
      setSnapshot(current => revision > current.revision
        ? { revision, entries: Object.freeze([...envelope.entries as FileReferenceEntry[]]) }
        : current);
    };
    window.addEventListener("message", receive);
    vscode.postMessage({ type: "requestFileReferenceCatalog" });
    return () => window.removeEventListener("message", receive);
  }, []);

  const index = useMemo(() => buildReferenceIndex(snapshot.entries), [snapshot.entries]);
  return <FileReferenceContext.Provider value={index}>{children}</FileReferenceContext.Provider>;
}

/** Static rendering and unwrapped tests intentionally receive an empty catalog. */
export function useFileReferenceIndex(): FileReferenceIndex {
  return useContext(FileReferenceContext);
}
