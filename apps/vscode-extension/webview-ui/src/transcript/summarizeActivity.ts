import type { ToolEventView } from "../types";
import type { ActivityStep } from "./groupTranscript";

interface Phrase { one: string; many: (count: number) => string }

// Tools that share a key are counted together in the summary line.
const PHRASES: ReadonlyMap<string, { key: string; phrase: Phrase }> = new Map([
  ["read_file", { key: "read", phrase: { one: "read a file", many: (n) => `read ${n} files` } }],
  ["search_code", { key: "search", phrase: { one: "searched code once", many: (n) => `searched code ${n} times` } }],
  ["search_semantic", { key: "search", phrase: { one: "searched code once", many: (n) => `searched code ${n} times` } }],
  ["list_directory", { key: "list", phrase: { one: "listed a folder", many: (n) => `listed ${n} folders` } }],
  ["run_command", { key: "run", phrase: { one: "ran a command", many: (n) => `ran ${n} commands` } }],
  ["start_session", { key: "run", phrase: { one: "ran a command", many: (n) => `ran ${n} commands` } }],
  ["write_stdin", { key: "run", phrase: { one: "ran a command", many: (n) => `ran ${n} commands` } }],
  ["web_search", { key: "web", phrase: { one: "searched the web once", many: (n) => `searched the web ${n} times` } }],
  ["web_fetch", { key: "fetch", phrase: { one: "fetched a page", many: (n) => `fetched ${n} pages` } }],
  ["query_graph", { key: "graph", phrase: { one: "queried the code graph once", many: (n) => `queried the code graph ${n} times` } }],
  ["dispatch_agents", { key: "agents", phrase: { one: "started an agent", many: (n) => `started ${n} agents` } }],
  ["write_todos", { key: "todos", phrase: { one: "updated the todo list", many: () => "updated the todo list" } }],
]);

const MAX_PHRASES = 3;
const EDITS_KEY = "edits";

const basename = (path: string): string => path.split("/").pop() || path;

interface ToolBucket { phrase: Phrase; count: number; paths: Set<string> | null }
interface EditRecord { path: string; additions: number; deletions: number; applied: boolean }

// Counts distinct files, not edit entries: two edits to one path are one file.
// A path counts as applied if any of its edits was applied; only applied edits add to +/−.
function describeEdits(edits: EditRecord[]): string {
  const files = new Map<string, { additions: number; deletions: number; applied: boolean }>();
  for (const edit of edits) {
    const file = files.get(edit.path) ?? { additions: 0, deletions: 0, applied: false };
    if (edit.applied) {
      file.additions += edit.additions;
      file.deletions += edit.deletions;
      file.applied = true;
    }
    files.set(edit.path, file);
  }
  const entries = [...files.entries()];
  if (entries.length === 1) {
    const [[path, file]] = entries;
    return file.applied
      ? `edited \`${basename(path)}\` +${file.additions} −${file.deletions}`
      : `edited \`${basename(path)}\` (not applied)`;
  }
  const additions = entries.reduce((sum, [, file]) => sum + file.additions, 0);
  const deletions = entries.reduce((sum, [, file]) => sum + file.deletions, 0);
  return `edited ${entries.length} files +${additions} −${deletions}`;
}

function describeBucket(bucket: ToolBucket): string {
  const total = bucket.paths ? bucket.paths.size + bucket.count : bucket.count;
  return total === 1 ? bucket.phrase.one : bucket.phrase.many(total);
}

export function summarizeActivity(steps: ActivityStep[]): string {
  const buckets = new Map<string, ToolBucket>();
  const edits: EditRecord[] = [];
  const order: string[] = [];
  let failed = 0;

  for (const step of steps) {
    if (step.type === "tools") {
      for (const event of step.events) {
        if (event.isError) failed += 1;
        const known = PHRASES.get(event.tool);
        const key = known?.key ?? `tool:${event.tool}`;
        if (!buckets.has(key)) {
          const phrase = known?.phrase ?? {
            one: `used ${event.tool} once`,
            many: (n: number) => `used ${event.tool} ${n} times`,
          };
          buckets.set(key, { phrase, count: 0, paths: event.tool === "read_file" ? new Set() : null });
          order.push(key);
        }
        const bucket = buckets.get(key)!;
        const path = typeof event.args?.path === "string" ? event.args.path : null;
        // Repeated reads of one file count once: the summary says how many files were read.
        if (bucket.paths && path) bucket.paths.add(path);
        else bucket.count += 1;
      }
    } else if (step.type === "edit" && step.entries.length) {
      if (!edits.length) order.push(EDITS_KEY);
      for (const entry of step.entries) {
        edits.push({ path: entry.path, additions: entry.additions, deletions: entry.deletions, applied: step.applied });
      }
    }
  }

  const phrases = order.map((key) => (key === EDITS_KEY ? describeEdits(edits) : describeBucket(buckets.get(key)!)));
  const overflow = phrases.length > MAX_PHRASES + 1;
  const shown = overflow ? [...phrases.slice(0, MAX_PHRASES), `and ${phrases.length - MAX_PHRASES} more`] : phrases;
  const text = shown.join(", ");
  const sentence = text.charAt(0).toUpperCase() + text.slice(1);
  return failed ? `${sentence} · ${failed} failed` : sentence;
}

export function describeTool(event: ToolEventView): string {
  const args = event.args ?? {};
  const str = (value: unknown): string => (typeof value === "string" ? value : "");
  switch (event.tool) {
    case "read_file":
      return `Reading \`${basename(str(args.path))}\``;
    case "list_directory":
      return `Listing \`${str(args.path) || "."}\``;
    case "search_code":
    case "search_semantic": {
      const query = str(args.pattern) || str(args.query);
      return query ? `Searching code for “${query}”` : "Searching code";
    }
    case "run_command":
    case "start_session": {
      const extra = Array.isArray(args.args) ? args.args.join(" ") : "";
      return `Running \`${[str(args.command), extra].filter(Boolean).join(" ")}\``;
    }
    case "web_search":
      return `Searching the web for “${str(args.query)}”`;
    case "web_fetch":
      return `Fetching ${str(args.url)}`;
    case "dispatch_agents":
      return "Starting agents";
    case "write_todos":
      return "Updating the todo list";
    default:
      return `Using ${event.tool}`;
  }
}
