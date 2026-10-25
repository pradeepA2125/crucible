import { constants, openSync, closeSync, readSync, fstatSync, realpathSync, lstatSync, watchFile, unwatchFile } from "node:fs";
import { containedPath } from "./security.js";
import { workspaceRelativePath } from "./workspace-files.js";
import { join, relative, isAbsolute } from "node:path";
import type { BackendTaskClient } from "@crucible/editor-client";
import type { MemoryDataSource } from "../../vscode-extension/src/memory-data.js";
import { GraphSnapshotStore } from "../../vscode-extension/src/graph/snapshot-store.js";
import type { GraphHostDeps } from "../../vscode-extension/src/graph/graph-messages.js";
/** Fixed project path, no symlink traversal, bounded bytes/rows before shared graph parsing. */
export function readInspectorSnapshot(root: string): string {
  const dir = join(root, ".crucible"), file = join(dir, "index-snapshot.json");
  if (lstatSync(dir).isSymbolicLink() || lstatSync(file).isSymbolicLink()) throw new Error("The index snapshot must be inside this project.");
  const rel = relative(realpathSync(root), realpathSync(file));
  if (isAbsolute(rel) || rel.startsWith("..")) throw new Error("The index snapshot is outside this project.");
  const fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const identity = fstatSync(fd), named = lstatSync(file);
    const verified = relative(realpathSync(root), realpathSync(file));
    if (lstatSync(dir).isSymbolicLink() || named.isSymbolicLink() || identity.dev !== named.dev || identity.ino !== named.ino || isAbsolute(verified) || verified.startsWith("..")) throw new Error("Index snapshot ownership changed. Refresh AXON.");
    const size = identity.size;
    if (!fstatSync(fd).isFile() || size > 32 * 1024 * 1024) throw new Error("Index snapshot exceeds the 32 MiB inspection limit.");
    const buffer = Buffer.alloc(size + 1); let bytes = 0;
    while (bytes < buffer.length) { const count = readSync(fd, buffer, bytes, buffer.length - bytes, null); if (!count) break; bytes += count; }
    if (bytes !== size) throw new Error("The index snapshot changed. Refresh AXON.");
    const text = new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, bytes));
    const raw = JSON.parse(text);
    if (raw.workspace_root !== realpathSync(root)) throw new Error("This index belongs to a different project. Rebuild it here.");
    if (!Array.isArray(raw?.graph?.nodes) || !Array.isArray(raw?.graph?.edges) || raw.graph.nodes.length > 50000 || raw.graph.edges.length > 150000) throw new Error("Index snapshot is malformed or exceeds the inspection limit.");
    return text;
  } finally { closeSync(fd); }
}
export function inspectorMemory(client: BackendTaskClient): MemoryDataSource {
  return { getInspect: id => id ? client.getMemoryInspect(id) : Promise.resolve(null), browse: filter => client.listMemories(filter), getChain: id => client.getSupersedeChain(id),
    getCuration: id => { if (!client.getMemoryCuration) throw new Error("Memory curation is unavailable."); return client.getMemoryCuration(id); },
    getSnapshot: id => { if (!client.getMemorySnapshot) throw new Error("Active memory is unavailable."); return client.getMemorySnapshot(id); } };
}
export function inspectorGraph(root: string, openFile: GraphHostDeps["openFile"], buildIndex: GraphHostDeps["buildIndex"]): GraphHostDeps & { watch(changed: () => void): () => void } {
  const store = new GraphSnapshotStore(join(root, ".crucible", "index-snapshot.json"), () => readInspectorSnapshot(root));
  return { loadModel: () => store.load(), staleAgeSec: () => { const generated = store.generatedAtMs(); const age = generated ? Math.floor((Date.now() - generated) / 1000) : null; return age !== null && age > 900 ? age : null; },
    watch: changed => { const file = join(root,".crucible","index-snapshot.json"); watchFile(file,{interval:1500,persistent:false},changed); return () => unwatchFile(file,changed); },
    fileDetail: id => store.fileDetail(id), symbolDetail: id => store.symbolDetail(id), searchSymbols: q => store.searchSymbols(q), openFile: async (path,line) => { workspaceRelativePath(path); await containedPath(root,path); await openFile(path,line); }, buildIndex };
}
