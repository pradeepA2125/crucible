import { resultReadyLabel } from "../../teams";
import type { TeamPostView } from "../../types";
import { ReportDisclosure } from "../shared/ReportDisclosure";
import { Avatar } from "./Avatar";
import { PostBody } from "./PostBody";

const OUTCOME: Record<string, string> = { actionable: "Action recommended", no_change: "No change recommended", needs_decision: "Decision needed" };

/** A published deliverable is passive content, never a proposal awaiting votes. */
export function ResultCard({ post, roster }: { post: TeamPostView; roster: string[] }) {
  const kind = typeof post.payload.deliverable_kind === "string" ? post.payload.deliverable_kind : null;
  const outcome = String(post.payload.outcome ?? "");
  return <article id={`team-post-${post.seq}`} data-testid={`team-result-${post.seq}`} data-author={post.author}
    className="relative grid min-w-0 gap-2 rounded-[10px] border px-3 py-2.5" style={{ borderColor: "var(--green-brd)", background: "var(--color-surface)" }}>
    <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
      <Avatar label={post.author} roster={roster} size="sm" />
      <strong className="text-text">{resultReadyLabel(kind)}</strong>
      <span className="text-text-3">{post.author} · run {String(post.payload.run_id ?? 1)}</span>
      {post.payload.basis_proposal_id != null && <span className="font-mono text-text-3">Based on {String(post.payload.basis_proposal_id)}</span>}
      {OUTCOME[outcome] && <span className="text-text-2">{OUTCOME[outcome]}</span>}
    </div>
    {typeof post.payload.summary === "string" && <p className="m-0 text-[12px] text-text-2">{post.payload.summary}</p>}
    <ReportDisclosure text={post.text} label={`${resultReadyLabel(kind)} by ${post.author}`} renderContent={(text) => <PostBody text={text} />} />
  </article>;
}
