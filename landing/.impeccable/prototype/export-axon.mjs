import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import ts from 'typescript';
const root = process.cwd();
const source = fs.readFileSync(path.join(root, 'apps/vscode-extension/src/graph/space-model.ts'), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { buildSpaceModel, resolveFileEdges } = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
const snapshot = JSON.parse(fs.readFileSync(path.join(root, '.crucible/index-snapshot.json'), 'utf8'));
const tracked = new Set(execFileSync('git', ['ls-files', '-z'], { cwd: root, encoding: 'utf8' }).split('\0'));
const relative = p => p.startsWith(snapshot.workspace_root + '/') ? p.slice(snapshot.workspace_root.length + 1) : p;
// No source contents, diagnostics, absolute paths, memory records or untracked project names.
const allowed = p => tracked.has(p) && /^(apps|services)\//.test(p) && /\.(ts|tsx|py|rs)$/.test(p) && !/(^|\/)(test|tests|fixtures|build|dist|node_modules)\//.test(p);
const nodes = snapshot.graph.nodes.filter(n => allowed(relative(n.path)));
const ids = new Set(nodes.map(n => n.id));
const edges = snapshot.graph.edges.filter(e => ids.has(e.from) && (ids.has(e.to) || e.to.startsWith('external:module:')));
const raw = { workspace_root: snapshot.workspace_root, generated_at_ms: snapshot.generated_at_ms, graph: { nodes, edges } };
const model = buildSpaceModel(raw);
model.workspaceRoot = 'Crucible / captured source graph';
const fileEdges = resolveFileEdges(raw);
const details = {};
for (const star of model.stars) {
  const symbols = nodes.filter(n => relative(n.path) === star.id && ['Function','Method','Class','Interface'].includes(n.kind)).slice(0, 32).map(n => ({ id: n.id, name: n.name, kind: n.kind, line: n.line ?? 1 }));
  const connections = fileEdges.filter(e => e.fromFile === star.id || e.toFile === star.id).map(e => {
    const outgoing = e.fromFile === star.id;
    const otherFile = outgoing ? e.toFile : e.fromFile;
    return { dir: outgoing ? 'out' : 'in', kind: e.kind, otherFile, crossPackage: model.stars.find(s => s.id === otherFile)?.pkg !== star.pkg };
  });
  const unique = [...new Map(connections.map(e => [e.dir + ':' + e.kind + ':' + e.otherFile, e])).values()];
  details[star.id] = { fileId: star.id, symbols, edges: unique, withinFileCount: 0 };
}
// Internal source IDs can contain absolute paths. Replace with stable demo-local ids.
for (const detail of Object.values(details)) detail.symbols.forEach((s, i) => { s.id = detail.fileId + '#symbol-' + i; });
const output = { model, details, provenance: { source: 'Existing Rust-indexer snapshot of tracked Crucible source files', capturedAt: new Date(snapshot.generated_at_ms).toISOString(), excludes: 'Tests, fixtures, untracked files, source text, diagnostics, absolute paths and all memory data', note: 'Captured subset, not a live index or a performance benchmark' } };
const out = path.join(root, 'landing/src/experience/assets/axon-capture.json');
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(output));
console.log(JSON.stringify({ output: path.relative(root, out), files: model.stars.length, packages: model.packages.length, bundles: model.bundles.length, bytes: fs.statSync(out).size }));
