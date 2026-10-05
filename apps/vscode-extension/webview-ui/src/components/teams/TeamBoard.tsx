import { useState, type ReactNode } from "react";
import { stanceOf } from "../../teams";
import type { TeamPostView } from "../../types";
import { useTeamsUi } from "./TeamsContext";

const MENTION_SPLIT = /(@[a-z0-9-]+)/;
const IS_MENTION = /^@[a-z0-9-]+$/;

/** Body text with @mentions highlighted. The text is the agent's; nothing in it is
 * interpreted — only rendered (spec §3.10: authors and kinds come from columns). */
function withMentions(text: string): ReactNode[] {
  return text.split(MENTION_SPLIT).map((part, i) =>
    IS_MENTION.test(part)
      ? <span key={i} data-mention={part.slice(1)} className="font-semibold"
          style={{ color: "var(--color-accent-ink)" }}>{part}</span>
      : <span key={i}>{part}</span>);
}

interface Assignment { member?: unknown; part?: unknown; files?: unknown }

function ProposalCard({ post, posts, members }: {
  post: TeamPostView; posts: TeamPostView[]; members: string[];
}) {
  const assignments = Array.isArray(post.payload.assignments)
    ? post.payload.assignments as Assignment[] : [];
  const shared = Array.isArray(post.payload.shared_files)
    ? post.payload.shared_files as string[] : [];
  return (
    <div data-author={post.author} className="surface-card px-3 py-2"
      style={post.closed ? { opacity: 0.55 } : undefined}>
      <div className="flex items-center gap-2 text-[11px]">
        <span className="rounded px-1.5 font-mono font-semibold"
          style={{ background: "var(--accent-bg)", color: "var(--color-accent-ink)" }}>
          P{post.seq}
        </span>
        <span className="font-semibold text-text">{post.author}</span>
        <span className="text-text-3">proposal{post.round !== null ? ` · round ${post.round}` : ""}</span>
        {post.closed && <span className="ml-auto text-text-3">{post.closed}</span>}
      </div>
      <div className="mt-1 whitespace-pre-wrap text-xs text-text">{withMentions(post.text)}</div>
      {assignments.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-[11px] text-text-2">
          {assignments.map((a, i) => (
            <li key={i}>{`${String(a.member)}: ${String(a.part)} — ${
              Array.isArray(a.files) ? (a.files as string[]).join(", ") : ""}`}</li>
          ))}
        </ul>
      )}
      {shared.length > 0 && (
        <div className="mt-1 text-[11px] text-text-3">{`shared: ${shared.join(", ")}`}</div>
      )}
      <div className="mt-1.5 flex flex-wrap gap-1.5 text-[10.5px]">
        {members.map((label) => {
          const stance = stanceOf(posts, post.seq, label);
          const word = stance === "agree" ? "agrees" : stance === "object" ? "objects" : "no stance";
          const color = stance === "agree" ? "var(--color-green)"
            : stance === "object" ? "var(--color-red)" : "var(--color-text-3)";
          return (
            <span key={label} aria-label={`${label} ${word}`} className="rounded-full px-1.5"
              style={{ border: "1px solid var(--color-border)", color }}>
              {label} {stance === "agree" ? "✓" : stance === "object" ? "✗" : "–"}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function evidenceLines(evidence: Record<string, unknown>): string[] {
  const lines: string[] = [];
  const files = Array.isArray(evidence.files) ? evidence.files as string[] : [];
  if (files.length > 0) {
    lines.push(typeof evidence.line === "number" ? `${files[0]}:${evidence.line}` : files[0],
               ...files.slice(1));
  }
  if (typeof evidence.command === "string") lines.push(`$ ${evidence.command}`);
  if (typeof evidence.output === "string") lines.push(evidence.output);
  if (typeof evidence.quote_seq === "number") lines.push(`quotes post #${evidence.quote_seq}`);
  return lines;
}

function PostLine({ post }: { post: TeamPostView }) {
  if (post.kind === "system") {
    return <div data-author="system" className="text-[11px] italic text-text-3">{post.text}</div>;
  }
  const isStance = post.kind === "agree" || post.kind === "object";
  const evidence = post.payload.evidence as Record<string, unknown> | undefined;
  return (
    <div data-author={post.author} className="text-xs">
      <div className="flex items-center gap-1.5 text-[11px]">
        <span className="font-semibold text-text">
          {post.recipient ? `${post.author} → ${post.recipient}` : post.author}
        </span>
        {isStance && (
          <span style={{ color: post.kind === "agree" ? "var(--color-green)" : "var(--color-red)" }}>
            {post.kind === "agree" ? "agrees with" : "objects to"} {post.refId}
          </span>
        )}
        {post.kind === "withdraw" && <span className="text-text-3">withdraws {post.refId}</span>}
      </div>
      {post.text && (
        <div className="whitespace-pre-wrap text-text-2">{withMentions(post.text)}</div>
      )}
      {evidence && (
        <details className="mt-0.5 text-[11px] text-text-3">
          <summary className="cursor-pointer">evidence</summary>
          {evidenceLines(evidence).map((line, i) => (
            <div key={i} className="whitespace-pre-wrap font-mono">{line}</div>
          ))}
        </details>
      )}
    </div>
  );
}

/** The board (spec v2 §9): posts in seq order. */
export function TeamBoard({ teamId }: { teamId: string }) {
  const ui = useTeamsUi();
  const [showMessages, setShowMessages] = useState(false);
  const team = ui.teams[teamId];
  const posts = ui.views[teamId]?.posts ?? [];
  const members = team?.members.map((m) => m.label) ?? [];
  const shown = posts.filter((p) => showMessages || p.recipient === null);
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <button type="button" aria-pressed={showMessages} onClick={() => setShowMessages((v) => !v)}
          className="cursor-pointer rounded-md px-2 py-0.5 text-[10.5px]"
          style={{ border: "1px solid var(--color-border)",
                   color: showMessages ? "var(--color-accent-ink)" : "var(--color-text-3)",
                   background: showMessages ? "var(--accent-bg)" : "transparent" }}>
          Messages
        </button>
      </div>
      {shown.length === 0 && <div className="text-[11px] text-text-3">No posts yet.</div>}
      {shown.map((p) => p.kind === "proposal"
        ? <ProposalCard key={p.seq} post={p} posts={posts} members={members} />
        : <PostLine key={p.seq} post={p} />)}
    </div>
  );
}
