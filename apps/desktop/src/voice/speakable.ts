/**
 * The last step before text reaches the synthesizer. The voice agent already writes for
 * listening; this catches what still leaks from screen text: code blocks, tables, list
 * markers, links, paths and identifiers.
 */

const CODE_NOTE = "There's a code example on screen.";
const SOURCE_EXTENSION = /\b([\w-]+)\.(?:tsx?|jsx?|mjs|cjs|py|rs|go|java|kt|swift|c|h|cpp|hpp|json|md|css|html|sh|toml|ya?ml|txt|lock)\b/g;
const ABBREVIATIONS = new Set(["e.g", "i.e", "etc", "vs", "cf", "approx", "mr", "mrs", "dr"]);

function stemOfPath(path: string): string {
  const last = path.replace(/\/+$/u, "").split("/").at(-1) ?? path;
  return last.replace(SOURCE_EXTENSION, "$1");
}

function spokenLine(line: string): string {
  return line
    .replace(/!?\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/`([^`]*)`/g, "$1")
    // Paths: an extension-bearing last segment, or at least two directories.
    .replace(/(?:\.{0,2}\/)?(?:[\w.~-]+\/)+[\w-]+\.\w+|(?:\.{0,2}\/)?(?:[\w.~-]+\/){2,}[\w.-]*/g, stemOfPath)
    .replace(SOURCE_EXTENSION, "$1")
    // Dotted identifiers (ControllerLoop._iterate), but not e.g. or v1.2.
    .replace(/\b[A-Za-z_]\w+(?:\.[A-Za-z_]\w+)+\b/g, token => token.replace(/\./g, " "))
    .replace(/(\w)_+(?=\w)/g, "$1 ")
    .replace(/(^|[\s(])_+(?=\w)/g, "$1")
    .replace(/(\w)_+(?=\W|$)/g, "$1")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[*#~]/g, "")
    .replace(/^\s*(?:\d+[.)]|[-+•])\s+/u, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function speakable(text: string): string {
  const withoutCode = text.replace(/```[\s\S]*?(?:```|$)/g, `\n${CODE_NOTE}\n`);
  const lines = withoutCode.split(/\r?\n/)
    .filter(line => !/^\s*\|/.test(line))
    .map(spokenLine)
    .filter(Boolean)
    .filter((line, index, all) => line !== CODE_NOTE || all[index - 1] !== CODE_NOTE);
  if (lines.length <= 1) return lines[0] ?? "";
  return lines.map(line => /[.!?:;,]$/.test(line) ? line : `${line}.`).join(" ");
}

/** The first `count` sentences, never split after an abbreviation, a version or a list number. */
export function firstSentences(text: string, count: number): string {
  const boundary = /[.!?](?=\s|$)/g;
  let found = 0;
  for (let match = boundary.exec(text); match; match = boundary.exec(text)) {
    const before = text.slice(0, match.index).split(/\s/).at(-1)?.toLowerCase() ?? "";
    if (ABBREVIATIONS.has(before) || /^\d+$/.test(before)) continue;
    found += 1;
    if (found === count) return text.slice(0, match.index + 1).trim();
  }
  return text.trim();
}
