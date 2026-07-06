export interface ComposerTrigger {
  kind: "slash" | "file";
  query: string;
  start: number;
  end: number;
}

// Mirrors slash.ts's parseSlashCommand grammar: a slash command is only valid as
// the ENTIRE leading token of the message (name chars: [A-Za-z0-9._-]). Once a
// space is typed after the name, this stops matching — the user is now typing
// args, and doSend()'s existing parseSlashCommand/expandPrompt flow takes over.
function detectSlash(text: string, cursor: number): ComposerTrigger | null {
  const head = text.slice(0, cursor);
  const match = /^\/([A-Za-z0-9._-]*)$/.exec(head.trimStart());
  if (!match) return null;
  const start = head.length - (match[0].length - 1); // offset of the name start (after "/")
  return { kind: "slash", query: match[1] ?? "", start: start - 1, end: cursor };
}

// An @-mention can start anywhere, as long as there's no whitespace between the
// "@" and the cursor (the token is still being typed).
function detectFile(text: string, cursor: number): ComposerTrigger | null {
  let i = cursor;
  while (i > 0 && text[i - 1] !== "@" && !/\s/.test(text[i - 1])) i--;
  if (i > 0 && text[i - 1] === "@") {
    return { kind: "file", query: text.slice(i, cursor), start: i - 1, end: cursor };
  }
  return null;
}

/** Detects an in-progress "/" or "@" trigger token ending exactly at `cursor`. */
export function detectTrigger(text: string, cursor: number): ComposerTrigger | null {
  return detectSlash(text, cursor) ?? detectFile(text, cursor);
}
