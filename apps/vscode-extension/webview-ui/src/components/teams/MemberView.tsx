import { useMemo, useState } from "react";
import { formatElapsed, isTerminalAgent } from "../../agents";
import { buildChapters, chapterHeading, type MemberChapter } from "../../teamChapters";
import { identityFor } from "../../teamIdentity";
import type { TeamPostView, ToolEventView } from "../../types";
import { MessageRow } from "../MessageRow";
import { AgentRow } from "../messages/AgentRow";
import { useAgentsUi } from "../agents/AgentsContext";
import { Avatar } from "./Avatar";
import { SaidPost, STATUS_CHIP, beatText } from "./Journey";
import { PostBody } from "./PostBody";
import { useTeamsUi } from "./TeamsContext";

const STATE_WORD: Record<string, string> = {
  running: "working", queued: "working", waiting: "needs your approval",
  awaiting_peer: "waiting on a teammate", failed: "failed", stopped: "stopped",
};

const firstLine = (text: string) =>
  text.split("\n").map((l) => l.replace(/[*_`#>]/g, "").trim()).find((l) => l) ?? "";

/** A member's own journey (spec 2026-10-05 §7). */
export function MemberView({ teamId, agentId }: { teamId: string; agentId: string }) {
  const agentsUi = useAgentsUi();
  const teamsUi = useTeamsUi();
  const team = teamsUi.teams[teamId];
  const tview = teamsUi.views[teamId];
  const aview = agentsUi.views[agentId];
  const row = agentsUi.agents[agentId];
  const member = team?.members.find((m) => m.agentId === agentId);
  const roster = useMemo(() => (team?.members ?? []).map((m) => m.label), [team]);
  const posts = tview?.posts ?? [];
  const status = row?.status ?? member?.status ?? "idle";
  const working = !isTerminalAgent(status);
  const chapters = useMemo(() => (member && aview
    ? buildChapters(member.label, aview.messages, tview?.activity ?? [], posts,
                    { working, fallbackStatus: status })
    : []), [member, aview, tview, posts, working, status]);
  const isProposal = (seq: number) => posts.some((p) => p.seq === seq && p.kind === "proposal");
  if (!member) return null;
  if (!aview) return <div className="text-[11px] text-text-3">Loading…</div>;

  const label = member.label;
  const wakes = (tview?.activity ?? []).filter((e) => e.label === label && e.kind === "woke").length;
  const tools = chapters.reduce((n, c) => n + c.tools, 0) + (working ? aview.live.length : 0);
  const active = chapters.reduce((n, c) => n + (c.durationMs ?? 0), 0);
  const mine = posts.filter((p) => p.author === label);
  const openProposals = posts.filter((p) => p.kind === "proposal" && p.closed === null);
  const stances = openProposals.map((p) => {
    const last = [...mine].reverse().find((q) => q.refId === `P${p.seq}` && (q.kind === "agree" || q.kind === "object"));
    return { id: `P${p.seq}`, stance: last?.kind ?? null };
  });
  return (
    <div className="grid gap-3.5">
      <section data-testid="member-profile" className="grid grid-cols-[auto_minmax(0,1fr)] gap-3 rounded-[10px] border border-border bg-surface p-3">
        <Avatar label={label} roster={roster} size="lg" ring={working ? "working" : "idle"} />
        <div className="grid min-w-0 gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-[14px]" style={{ color: identityFor(label, roster).color }}>{label}</strong>
            {member.name && <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }}>{member.name}</span>}
            <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: working ? "var(--color-accent-ink)" : "var(--color-text-3)", borderColor: "var(--color-border-strong)" }}>
              ● {STATE_WORD[status] ?? "idle"}
            </span>
          </div>
          {member.description && <div className="text-[12px] text-text-2">{member.description}</div>}
          <div className="flex flex-wrap gap-3.5 text-[11px] text-text-3">
            <span><b className="text-text">{chapters.length}</b> chapter{chapters.length === 1 ? "" : "s"}</span>
            <span><b className="text-text">{wakes}</b> wake{wakes === 1 ? "" : "s"}</span>
            <span><b className="text-text">{tools}</b> tools</span>
            <span><b className="text-text">{mine.filter((p) => p.recipient === null && p.kind === "post").length}</b> posts</span>
            <span><b className="text-text">{mine.filter((p) => p.recipient !== null).length}</b> messages</span>
            {active > 0 && <span><b className="text-text">{formatElapsed(active)}</b> active</span>}
          </div>
          {stances.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-text-3">Stances
              {stances.map((s) => (
                <span key={s.id} className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]"
                  style={{ color: s.stance === "agree" ? "var(--color-green)" : s.stance === "object" ? "var(--color-red)" : "var(--color-text-3)", borderColor: "var(--color-border-strong)" }}>
                  {s.id} {s.stance === "agree" ? "✓ agreed" : s.stance === "object" ? "✗ objected" : "– no stance"}
                </span>
              ))}
            </div>
          )}
        </div>
      </section>
      <div className="relative grid gap-2.5 pl-[22px] before:absolute before:bottom-2 before:left-[7px] before:top-2 before:w-[1.5px] before:bg-[var(--color-border-strong)] before:content-['']">
        {chapters.map((c, i) => (
          <Chapter key={c.n} chapter={c} defaultOpen={i === chapters.length - 1} label={label} roster={roster}
            posts={posts} isProposal={isProposal} live={c.current ? aview.live : []} now={row?.now ?? ""} />
        ))}
      </div>
    </div>
  );
}

function Chapter({ chapter: c, defaultOpen, label, roster, posts, isProposal, live, now }: {
  chapter: MemberChapter; defaultOpen: boolean; label: string; roster: string[]; posts: TeamPostView[];
  isProposal: (seq: number) => boolean; live: ToolEventView[]; now: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [showWork, setShowWork] = useState(false);
  const chip = STATUS_CHIP[c.status];
  const color = identityFor(label, roster).color;
  const postBySeq = (seq: number) => posts.find((p) => p.seq === seq);
  const ref = (seq: number) => (isProposal(seq) ? `P${seq}` : `#${seq}`);
  return (
    <div data-testid={`chapter-${c.n}`} className="relative rounded-[10px] border border-border bg-surface">
      <span aria-hidden="true" className="absolute left-[-24px] top-[9px] grid h-[18px] w-[18px] place-items-center rounded-full text-[10px] font-semibold"
        style={{ background: color, color: "var(--color-panel)", boxShadow: "0 0 0 3px var(--color-surface-2)" }}>{c.n}</span>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left text-text">
        <span className="text-text-3">{open ? "▾" : "▸"}</span>
        <strong>{chapterHeading(c, isProposal).title}</strong>{" "}
        <span className="text-[12px] text-text-2">· {chapterHeading(c, isProposal).why}</span>
        <span className="ml-auto flex items-center gap-2">
          {c.start && <span className="text-[10.5px] tabular-nums text-text-3">{new Date(c.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
          {c.durationMs !== null && <span className="text-[11px] text-text-3">{formatElapsed(c.durationMs)}</span>}
          {c.current
            ? <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }}>● working</span>
            : chip && <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: chip.color, background: chip.bg, borderColor: "var(--color-border-strong)" }}>{chip.text}</span>}
        </span>
      </button>
      {!open && c.report && <div className="px-3 pb-2.5 pl-[30px] text-[11.5px] text-text-3">{firstLine(c.report)}</div>}
      {open && (
        <div className="grid gap-2.5 px-3 pb-3">
          {c.handed.length > 0 && (
            <div className="grid gap-1.5">
              <span className="text-[10px] uppercase tracking-[.08em] text-text-3">Handed · {c.handed.length} post{c.handed.length === 1 ? "" : "s"}</span>
              <div data-testid={`handed-${c.n}`} className="flex flex-wrap gap-1.5">
                {c.handed.map((seq) => {
                  const p = postBySeq(seq);
                  return (
                    <span key={seq} className="inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-[11.5px] text-text-2" style={{ background: "var(--color-panel)", borderColor: "var(--color-border-strong)" }}>
                      <span className="font-mono text-[10.5px] font-semibold text-text-3">{ref(seq)}</span>
                      {p && <><Avatar label={p.author} roster={roster} size="sm" /><span className="min-w-0 truncate">{p.author} · {firstLine(p.text)}</span></>}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
          {c.pickedUp.map((pu) => (
            <div key={pu.at} className="text-[11.5px] text-text-2">
              <span style={{ color: "var(--color-code)" }}>↪ </span>
              {beatText({ teamId: "", aseq: 0, at: pu.at, label, kind: "picked_up", activation: c.n, causeSeq: null, payload: { posts: pu.posts } }, isProposal).text}
            </div>
          ))}
          {(c.work.length > 0 || live.length > 0) && (
            <div className="grid gap-1.5">
              <span className="text-[10px] uppercase tracking-[.08em] text-text-3">Work</span>
              <button type="button" onClick={() => setShowWork(!showWork)}
                className="justify-self-start rounded-md border border-dashed px-2.5 py-1 text-[11.5px] text-text-2" style={{ borderColor: "var(--color-border-strong)" }}>
                Explored · {c.tools + live.length} tool{c.tools + live.length === 1 ? "" : "s"} {showWork ? "▾" : "▸"}
              </button>
              {showWork && <div className="grid gap-1">{c.work.map((m, i) => <MessageRow key={i} msg={m} />)}
                {live.length > 0 && <AgentRow content="" toolEvents={live} />}</div>}
            </div>
          )}
          {c.said.length > 0 && (
            <div className="grid gap-1.5">
              <span className="text-[10px] uppercase tracking-[.08em] text-text-3">Said</span>
              {c.said.map((p) => <SaidPost key={p.seq} post={p} roster={roster} />)}
            </div>
          )}
          {c.report && (
            <div className="grid gap-1.5 rounded-[9px] border px-3 py-2"
              style={{ borderColor: "var(--green-brd)", background: "linear-gradient(180deg, var(--green-bg), transparent 60%), var(--color-panel)" }}>
              <div className="flex items-center gap-2 text-[11.5px]">{chip && <span style={{ color: chip.color }}>{chip.text}</span>}<strong>Report</strong></div>
              <PostBody text={c.report} />
            </div>
          )}
          {c.current && (
            <div className="flex items-center gap-2 text-[11.5px]" style={{ color: "var(--color-accent-ink)" }}>
              <span className="h-[7px] w-[7px] rounded-full" style={{ background: "var(--color-accent)", boxShadow: "0 0 0 3px var(--accent-bg-2)" }} />
              working{now ? ` — ${now}` : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
