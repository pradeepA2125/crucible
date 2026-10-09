import { matchReference, parseReference, type FileReferenceIndex } from "../../../src/file-references/matching";

type Node = {
  type: string;
  value?: string;
  url?: string;
  children?: Node[];
  data?: { hProperties?: Record<string, string> };
};

const SKIPPED = new Set(["link", "linkReference", "image", "imageReference", "code", "html", "definition"]);
const LEADING = /^[([{"']+/u;
const TRAILING = /[.,;:!?\])}"']+$/u;

function candidate(token: string): { prefix: string; value: string; suffix: string } {
  const prefix = token.match(LEADING)?.[0] ?? "";
  const withoutPrefix = token.slice(prefix.length);
  const suffix = withoutPrefix.match(TRAILING)?.[0] ?? "";
  return { prefix, value: withoutPrefix.slice(0, withoutPrefix.length - suffix.length), suffix };
}

function link(value: string, inline: boolean): Node {
  return {
    type: "link",
    url: "#workspace-file-reference",
    data: { hProperties: { "data-file-reference": value } },
    children: [{ type: inline ? "inlineCode" : "text", value }],
  };
}

function split(node: Node, index: FileReferenceIndex): Node[] {
  const value = node.value ?? "";
  const inline = node.type === "inlineCode";
  const output: Node[] = [];
  const parts = value.split(/(\s+)/u);
  for (const part of parts) {
    if (!part) continue;
    if (/^\s+$/u.test(part)) { output.push({ type: node.type, value: part }); continue; }
    const exact = matchReference(index, part).length ? part : "";
    const trimmed = candidate(part);
    const matched = exact || (parseReference(trimmed.value) && matchReference(index, trimmed.value).length ? trimmed.value : "");
    if (!matched) { output.push({ type: node.type, value: part }); continue; }
    if (!exact && trimmed.prefix) output.push({ type: node.type, value: trimmed.prefix });
    output.push(link(matched, inline));
    if (!exact && trimmed.suffix) output.push({ type: node.type, value: trimmed.suffix });
  }
  return output;
}

function walk(node: Node, index: FileReferenceIndex): void {
  // Authored workspace links need the same activation bridge as auto-linked prose.
  // Leave their labels/children intact and never reinterpret external URLs.
  if (node.type === "link") {
    if (matchReference(index, node.url).length) {
      node.data = { ...node.data, hProperties: {
        ...node.data?.hProperties, "data-file-reference": node.url!,
      } };
      node.url = "#workspace-file-reference";
    }
    return;
  }
  if (SKIPPED.has(node.type) || !node.children) return;
  const children: Node[] = [];
  for (const child of node.children) {
    if (child.type === "text" || child.type === "inlineCode") {
      children.push(...split(child, index));
    } else {
      walk(child, index);
      children.push(child);
    }
  }
  node.children = children;
}

/** Remark plugin for exact, catalog-backed workspace file references only. */
export function remarkFileReferences(options: { index: FileReferenceIndex }) {
  return (tree: unknown): void => {
    if (tree && typeof tree === "object" && !Array.isArray(tree)) walk(tree as Node, options.index);
  };
}
