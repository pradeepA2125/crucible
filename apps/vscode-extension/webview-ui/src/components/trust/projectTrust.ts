import { useCallback, useEffect, useState } from "react";
import { vscode } from "../../vscodeApi";
import type { DiffLine } from "../shared/DiffPanes";

/** Repository files that change Crucible's behavior; each counts only once trusted
 * (prompt-injection Phase 1). Mirrors editor-client's ProjectTrustFile. */
import type { TrustKind } from "../../types";
export type { TrustKind };
export interface TrustFile {
  kind: TrustKind;
  state: "absent" | "trusted" | "untrusted";
  sha256: string | null;
  /** Invisible characters arrive as visible ⟦U+XXXX⟧ markers. */
  content: string | null;
  diffBase: string | null;
}
export interface TrustView { files: TrustFile[]; error?: string }

export const TRUST_LABEL: Record<TrustKind, string> = {
  agents_md: "AGENTS.md",
  skills: "this project's skills",
  mcp: "mcp.json",
  approved_commands: "remembered commands",
  approved_mcp_tools: "remembered MCP tools",
  approved_web_fetch: "remembered web pages",
  approved_web_search: "remembered web searches",
};
export const TRUST_PATH: Record<TrustKind, string> = {
  agents_md: "AGENTS.md",
  skills: ".crucible/skills",
  mcp: ".crucible/mcp.json",
  approved_commands: ".crucible/approved-commands.json",
  approved_mcp_tools: ".crucible/approved-mcp-tools.json",
  approved_web_fetch: ".crucible/approved-web-fetch.json",
  approved_web_search: ".crucible/approved-web-search.json",
};
/** What trusting the file lets it do, said where the user decides. */
export const TRUST_EFFECT: Record<TrustKind, string> = {
  agents_md: "Trusted, it becomes project instructions the agent follows. Until then the agent reads it as notes.",
  skills: "Trusted, the agent can load these skills and follow them. Until then they're unavailable.",
  mcp: "Trusted, its enabled servers connect, and stdio servers run their commands on this computer. Until then none start.",
  approved_commands: "Trusted, these commands run without asking. Until then they're ignored.",
  approved_mcp_tools: "Trusted, these MCP tools run without asking. Until then they're ignored.",
  approved_web_fetch: "Trusted, these pages are fetched without asking. Until then they're ignored.",
  approved_web_search: "Trusted, these searches run without asking. Until then they're ignored.",
};

export function untrustedFiles(files: TrustFile[]): TrustFile[] {
  return files.filter(f => f.state === "untrusted");
}

/** "AGENTS.md and mcp.json aren't trusted yet". */
export function trustNames(files: TrustFile[]): string {
  const names = files.map(f => TRUST_LABEL[f.kind]);
  if (names.length === 0) return "";
  if (names.length === 1) return `${names[0]} isn't trusted yet`;
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} aren't trusted yet`;
}

// Above this many table cells the LCS diff would stall the webview; show the full file.
const MAX_DIFF_CELLS = 2_000_000;

/** Line diff of the last trusted copy against the current file (LCS), or null when the
 * files are too large to diff here. */
export function lineDiff(base: string, current: string): DiffLine[] | null {
  const a = base.split("\n"), b = current.split("\n");
  const n = a.length, m = b.length;
  if ((n + 1) * (m + 1) > MAX_DIFF_CELLS) return null;
  const table: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      out.push({ kind: "ctx", num: String(j + 1), marker: " ", text: a[i], oldNumber: i + 1, newNumber: j + 1 });
      i++; j++;
    } else if (i < n && (j >= m || table[i + 1][j] >= table[i][j + 1])) {
      // A removed line comes before the line that replaced it.
      out.push({ kind: "del", num: "", marker: "-", text: a[i], oldNumber: i + 1 });
      i++;
    } else {
      out.push({ kind: "add", num: String(j + 1), marker: "+", text: b[j], newNumber: j + 1 });
      j++;
    }
  }
  return out;
}

/** How a host carries trust: the chat panel and Settings use different message names. */
export interface TrustChannel {
  read(): void;
  set(kind: TrustKind, sha256: string): void;
  /** Turns a host message into a view, or null when it isn't one. */
  parse(message: unknown): TrustView | null;
}

/** The chat panel's channel: projectTrustRead / projectTrustSet → projectTrust. */
export const CHAT_TRUST_CHANNEL: TrustChannel = {
  read: () => vscode.postMessage({ type: "projectTrustRead" }),
  set: (kind, sha256) => vscode.postMessage({ type: "projectTrustSet", kind, sha256 }),
  parse: message => {
    const m = message as { type?: unknown; files?: unknown; error?: unknown } | null;
    if (m?.type !== "projectTrust" || !Array.isArray(m.files)) return null;
    return { files: m.files as TrustFile[], ...(typeof m.error === "string" ? { error: m.error } : {}) };
  },
};

/** Current trust state from the host, re-read on mount; `trust` posts and waits for the next view. */
export function useProjectTrust(channel: TrustChannel = CHAT_TRUST_CHANNEL) {
  const [view, setView] = useState<TrustView | null>(null);
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const next = channel.parse(event.data);
      if (next) setView(next);
    };
    window.addEventListener("message", onMessage);
    channel.read();
    return () => window.removeEventListener("message", onMessage);
  }, [channel]);
  const trust = useCallback((kind: TrustKind, sha256: string) => channel.set(kind, sha256), [channel]);
  return { view, trust };
}
