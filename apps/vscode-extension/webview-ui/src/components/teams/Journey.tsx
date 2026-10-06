import { useMemo, useState } from "react";
import { elapsedMs, formatElapsed, isTerminalAgent } from "../../agents";
import { isTerminalTeam } from "../../teams";
import { identityFor } from "../../teamIdentity";
import { DEFAULT_FILTERS, buildJourney, type JourneyFilters, type JourneyItem, type TallyChip } from "../../teamJourney";
import type { TeamActivityView, TeamPostView } from "../../types";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { Avatar } from "./Avatar";
import { PostBody } from "./PostBody";
import { AdoptedCard, RoundStrip, Verdict, heldText } from "./RoundItems";
import { useTeamsUi } from "./TeamsContext";

const clock = (at: string) => new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function Name({ label, roster }: { label: string; roster: string[] }) {
  return <span className="font-semibold" style={{ color: identityFor(label, roster).color }}>{label}</span>;
}

function Seq({ seq }: { seq: number }) {
  return <span className="font-mono text-[10.5px] font-semibold text-text-3">#{seq}</span>;
}

function Time({ at }: { at: string }) {
  return <span className="ml-auto whitespace-nowrap text-[10.5px] tabular-nums text-text-3" title={new Date(at).toLocaleString()}>{clock(at)}</span>;
}

/** The spine's dot for an item, coloured by its author. */
function Dot({ color, shape = "round" }: { color: string; shape?: "round" | "square" }) {
  return <span aria-hidden="true" className="absolute left-[-19px] top-[13px] h-[9px] w-[9px]"
    style={{ background: color, borderRadius: shape === "round" ? "50%" : 2, boxShadow: "0 0 0 3px var(--color-surface-2)" }} />;
}

const STANCE_WORD = { agree: "agrees", object: "objects", pending: "no stance yet" } as const;
const WAS_WORD = { agree: "agreeing", object: "objecting" } as const;

function Tally({ chips, roster }: { chips: TallyChip[]; roster: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] text-text-3">Stances</span>
      {chips.map((c) => {
        const tone = c.stance === "agree" ? "var(--color-green)" : c.stance === "object" ? "var(--color-red)" : "var(--color-text-3)";
        const name = `${c.label} ${STANCE_WORD[c.stance]}${c.was ? `, was ${WAS_WORD[c.was]}` : ""}`;
        return (
          <button key={c.label} type="button" aria-label={name}
            onClick={() => c.seq !== null && document.getElementById(`team-post-${c.seq}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}
            className="inline-flex h-[18px] items-center gap-1 rounded-full border px-1.5 text-[10.5px]"
            style={{ color: tone, borderColor: "var(--color-border-strong)" }}>
            <Avatar label={c.label} roster={roster} size="sm" />
            {c.stance === "agree" ? "✓" : c.stance === "object" ? "✗" : "–"}
            {c.was && <span className="text-text-3">(was {c.was === "agree" ? "✓" : "✗"})</span>}
          </button>
        );
      })}
    </div>
  );
}

function PostCard({ post, footer, tally, roster }: Omit<Extract<JourneyItem, { kind: "post" }>, "key"> & { roster: string[] }) {
  const [open, setOpen] = useState(false);
  const long = post.text.split("\n").length > 6 || post.text.length > 420;
  const assignments = Array.isArray(post.payload.assignments) ? post.payload.assignments as { member?: string; part?: string; files?: string[] }[] : [];
  return (
    <article id={`team-post-${post.seq}`} data-author={post.author}
      className="relative grid gap-1.5 rounded-[10px] border border-border bg-surface px-3 py-2.5">
      <Dot color={identityFor(post.author, roster).color} />
      <div className="flex flex-wrap items-center gap-2">
        <Avatar label={post.author} roster={roster} size="sm" />
        <Name label={post.author} roster={roster} />
        {post.recipient && <><span className="text-text-3">→</span><Avatar label={post.recipient} roster={roster} size="sm" /><Name label={post.recipient} roster={roster} /></>}
        {post.kind === "proposal"
          ? <span className="rounded border px-1.5 font-mono text-[11px] font-semibold" style={{ color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }}>P{post.seq}</span>
          : <Seq seq={post.seq} />}
        <span className="text-[11px] text-text-3">{post.payload.closing === true ? `closing proposal · review cycle ${String(post.payload.cycle)}` : post.kind === "proposal" ? "proposal" : post.recipient ? "direct message" : post.author === "main" ? "post · from you, via main" : "post"}</span>
        <Time at={post.createdAt} />
      </div>
      <div className={long && !open ? "max-h-[7.6em] overflow-hidden [mask-image:linear-gradient(#000_70%,transparent)]" : undefined}>
        <PostBody text={post.text} />
      </div>
      {long && <button type="button" className="justify-self-start text-[11px] text-accent-ink" onClick={() => setOpen(!open)}>{open ? "Show less" : "Show more"}</button>}
      {assignments.length > 0 && (
        <div className="grid gap-1 rounded-lg border px-2.5 py-2 text-[11.5px]" style={{ background: "var(--color-panel)", borderColor: "var(--hairline)" }}>
          {assignments.map((a, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <span className="w-16 text-[10px] uppercase tracking-[.08em] text-text-3">Assigns</span>
              <Avatar label={String(a.member)} roster={roster} size="sm" /><Name label={String(a.member)} roster={roster} />
              <span className="text-text-2">{a.part}</span>
              {(a.files ?? []).map((f) => <code key={f} className="font-mono text-[11px] text-[var(--color-code)]">{f}</code>)}
            </div>
          ))}
        </div>
      )}
      {post.payload.closing === true && typeof post.payload.files_changed === "object" && post.payload.files_changed !== null && (
        <div data-testid={`closing-files-${post.seq}`} className="grid gap-0.5 text-[11.5px] text-text-2">
          {Object.entries(post.payload.files_changed as Record<string, string[]>).map(([lb, files]) => (
            <div key={lb}>{lb}: {files.join(", ") || "no files"}</div>
          ))}
        </div>
      )}
      {tally && <Tally chips={tally} roster={roster} />}
      {(footer.woke.length > 0 || footer.queued.length > 0 || footer.held) && (
        <div data-testid={`footer-p${post.seq}`} className="flex flex-wrap items-center gap-1.5 border-t pt-1.5 text-[11px] text-text-3" style={{ borderColor: "var(--hairline)" }}>
          {footer.woke.length > 0 && <><span style={{ color: "var(--color-amber)" }}>⚡</span>woke {footer.woke.map((l, i) => <span key={l} className="inline-flex items-center gap-1">{i > 0 && " · "}<Avatar label={l} roster={roster} size="sm" />{l}</span>)}</>}
          {footer.queued.length > 0 && <><span style={{ color: "var(--color-code)" }}>↪</span>queued for {footer.queued.map((l, i) => <span key={l} className="inline-flex items-center gap-1">{i > 0 && " · "}<Avatar label={l} roster={roster} size="sm" />{l}</span>)}<span>· it was working — picks this up at its next step</span></>}
          {footer.held && <><span>→</span><span className="text-text-3">{heldText(footer.held)}</span></>}
        </div>
      )}
    </article>
  );
}

function StanceReply({ post, replaces, roster }: Omit<Extract<JourneyItem, { kind: "stance" }>, "key"> & { roster: string[] }) {
  const tone = post.kind === "agree" ? "var(--color-green)" : post.kind === "object" ? "var(--color-red)" : "var(--color-text-3)";
  const evidence = post.payload.evidence as { files?: string[]; line?: number; command?: string; output?: string; quote_seq?: number } | undefined;
  const verb = post.kind === "agree" ? "agrees with" : post.kind === "object" ? "objects to" : "withdraws";
  return (
    <div id={`team-post-${post.seq}`} data-author={post.author}
      className="ml-3 grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-2 rounded-r-lg py-1.5 pl-2.5 pr-2.5"
      style={{ borderLeft: `2px solid ${tone}`, background: "linear-gradient(90deg, var(--hairline), transparent)" }}>
      <Avatar label={post.author} roster={roster} size="sm" />
      <div className="grid min-w-0 gap-0.5">
        <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
          <Name label={post.author} roster={roster} />
          <span style={{ color: tone }}>{post.kind === "agree" ? "✓ " : post.kind === "object" ? "✗ " : ""}</span>
          <span style={{ color: tone }}>{verb}</span>
          <button type="button" className="font-mono text-[10.5px] font-semibold text-accent-ink"
            onClick={() => document.getElementById(`team-post-${Number(String(post.refId).slice(1))}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>{post.refId}</button>
          <Seq seq={post.seq} />
          {replaces !== null && <span className="text-[11px] text-text-3">replaces #{replaces}</span>}
        </div>
        {post.text && <PostBody text={post.text} className="text-text-2" />}
        {evidence && (
          <details className="text-[11px] text-text-3">
            <summary className="cursor-pointer">evidence</summary>
            {evidence.files && evidence.files.length > 0 && <div className="font-mono">{evidence.line !== undefined ? `${evidence.files[0]}:${evidence.line}` : evidence.files[0]}</div>}
            {evidence.command && <div className="font-mono">$ {evidence.command}</div>}
            {evidence.output && <div className="whitespace-pre-wrap font-mono">{evidence.output}</div>}
            {evidence.quote_seq !== undefined && <div>quotes post #{evidence.quote_seq}</div>}
          </details>
        )}
      </div>
      <Time at={post.createdAt} />
    </div>
  );
}

/** A post as shown inside a member's chapter: same identity and body, no spine dot. */
export function SaidPost({ post, roster }: { post: TeamPostView; roster: string[] }) {
  if (post.kind === "agree" || post.kind === "object" || post.kind === "withdraw") {
    return <StanceReply kind="stance" at={post.createdAt} post={post} replaces={null} roster={roster} />;
  }
  return (
    <article className="grid gap-1 rounded-[10px] border border-border bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Avatar label={post.author} roster={roster} size="sm" />
        <Name label={post.author} roster={roster} />
        {post.recipient && <><span className="text-text-3">→</span><Name label={post.recipient} roster={roster} /></>}
        <Seq seq={post.seq} />
        <span className="text-[11px] text-text-3">{post.recipient ? "direct message" : post.kind}</span>
        <Time at={post.createdAt} />
      </div>
      <PostBody text={post.text} />
    </article>
  );
}

const CAUSE_TEXT: Record<string, (by: string, seq: string) => string> = {
  kickoff: () => "kickoff",
  mention: (by, seq) => `mentioned by ${by} in ${seq}`,
  team_mention: (by, seq) => `@team from ${by} in ${seq}`,
  message: (by, seq) => `direct message from ${by} ${seq}`,
  main_post: (_by, seq) => `main's post ${seq}`,
  leftover: () => "input that arrived after its last step",
};

/** Plain wording for a lifecycle beat (spec 2026-10-05 §6). */
export function beatText(
  e: TeamActivityView, isProposal: (seq: number) => boolean = () => false,
): { icon: string; tone: string; text: string } {
  const p = e.payload;
  const seqs = (Array.isArray(p.posts) ? p.posts as number[] : []);
  const list = seqs.map((s) => (isProposal(s) ? `P${s}` : `#${s}`)).join(" ");
  const by = String(p.by ?? "");
  const seq = p.post_seq !== undefined && p.post_seq !== null ? `#${String(p.post_seq)}` : "";
  switch (e.kind) {
    case "took_up":
      return { icon: "▶", tone: "var(--color-accent)", text: seqs.length
        ? `${e.label} took up ${list} · ${seqs.length} new post${seqs.length === 1 ? "" : "s"}`
        : `${e.label} started · nothing new on the board` };
    case "picked_up":
      return { icon: "↪", tone: "var(--color-code)", text: `${e.label} picked up ${list} while working` };
    case "woke":
      return { icon: "⚡", tone: "var(--color-amber)", text: `${e.label} woke — ${(CAUSE_TEXT[String(p.cause)] ?? (() => String(p.cause)))(by, seq)}` };
    case "notified":
      return { icon: "↪", tone: "var(--color-code)", text: `${e.label} was working — ${seq} from ${by} queued for its next step` };
    case "capped":
      return { icon: "⏸", tone: "var(--color-text-3)", text: `${e.label} was not woken — ${String(p.wakes)} wakes this phase (limit ${String(p.cap)})` };
    case "requeued":
      return { icon: "↻", tone: "var(--color-amber)", text: `${e.label} restarted after a provider error (retry ${String(p.retry)} of ${String(p.of)})` };
    case "deadline":
      return { icon: "⏱", tone: "var(--color-amber)", text: `${e.label} hit the round's time limit — reporting what it has` };
    default:
      return { icon: "·", tone: "var(--color-text-3)", text: `${e.label} ${e.kind}` };
  }
}

function Beat({ event, isProposal }: { event: TeamActivityView; isProposal: (seq: number) => boolean }) {
  const b = beatText(event, isProposal);
  return (
    <div data-testid={`beat-a${event.aseq}`} className="relative flex min-h-5 items-center gap-2 text-[11.5px] text-text-2">
      <span aria-hidden="true" className="absolute left-[-17px] top-1/2 -mt-[2.5px] h-[5px] w-[5px] rounded-full" style={{ background: "var(--color-text-4)" }} />
      <span className="w-4 flex-none text-center" style={{ color: b.tone }}>{b.icon}</span>
      <span className="min-w-0 truncate">{b.text}</span>
      <Time at={event.at} />
    </div>
  );
}

export const STATUS_CHIP: Record<string, { text: string; color: string; bg: string }> = {
  completed: { text: "✓ completed", color: "var(--color-green)", bg: "var(--green-bg)" },
  awaiting_peer: { text: "⏳ waiting on a teammate", color: "var(--color-text-2)", bg: "transparent" },
  partial: { text: "◐ partial", color: "var(--color-amber)", bg: "var(--amber-bg)" },
  failed: { text: "✗ failed", color: "var(--color-red)", bg: "var(--red-bg)" },
  failed_transient: { text: "⚠ provider unavailable", color: "var(--color-amber)", bg: "var(--amber-bg)" },
  stopped: { text: "■ stopped", color: "var(--color-text-3)", bg: "transparent" },
};

export function wrapStats(p: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof p.duration_ms === "number") parts.push(formatElapsed(p.duration_ms));
  if (typeof p.tools === "number") parts.push(`${p.tools} tool${p.tools === 1 ? "" : "s"}`);
  for (const [key, word] of [["posts", "post"], ["messages", "message"], ["stances", "stance"]] as const) {
    const n = p[key];
    if (typeof n === "number" && n > 0) parts.push(`${n} ${word}${n === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}

function WrapRow({ event, roster }: { event: TeamActivityView; roster: string[] }) {
  const [open, setOpen] = useState(false);
  const p = event.payload;
  const status = String(p.status ?? "completed");
  const chip = STATUS_CHIP[status] ?? STATUS_CHIP.completed;
  const report = String(p.report ?? "");
  return (
    <div data-testid={`wrap-a${event.aseq}`} className="relative grid gap-1.5 rounded-lg border border-border px-2.5 py-1.5" style={{ background: "var(--color-panel)" }}>
      <Dot color={identityFor(event.label, roster).color} shape="square" />
      <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
        <Avatar label={event.label} roster={roster} size="sm" />
        <Name label={event.label} roster={roster} />{" "}<span>{typeof p.round === "number" ? `wrapped up round ${p.round}` : "wrapped up"}</span>
        <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: chip.color, background: chip.bg, borderColor: "var(--color-border-strong)" }}>{chip.text}</span>
        <span className="tabular-nums text-text-3">{wrapStats(p)}</span>
        {typeof p.reason === "string" && <span className="text-text-3">— {p.reason}</span>}
        {report && (
          <button type="button" aria-label={`${open ? "Hide" : "Show"} ${event.label}'s report`} onClick={() => setOpen(!open)}
            className="ml-auto text-[11px] text-accent-ink">report {open ? "▾" : "▸"}</button>
        )}
      </div>
      {open && <div className="border-t pt-1.5" style={{ borderColor: "var(--hairline)" }}><PostBody text={report} /></div>}
    </div>
  );
}

function NowStrip({ teamId, roster }: { teamId: string; roster: string[] }) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const team = teamsUi.teams[teamId];
  const rows = (team?.members ?? []).map((m) => ({ m, row: agentsUi.agents[m.agentId] }));
  const running = rows.some(({ m, row }) => !isTerminalAgent(row?.status ?? m.status));
  const now = useNow(running);
  return (
    <div data-testid="now-strip" aria-live="polite" className="sticky bottom-[-12px] -mx-3.5 -mb-3 mt-3 grid gap-1 px-3.5 pb-3 pt-2.5"
      style={{ background: "linear-gradient(transparent, var(--color-surface-2) 30%)" }}>
      {rows.map(({ m, row }) => {
        const status = row?.status ?? m.status;
        if (!isTerminalAgent(status)) {
          const ms = row ? elapsedMs(row, now) : null;
          return (
            <div key={m.label} className="flex items-center gap-2 text-[11.5px]" style={{ color: "var(--color-accent-ink)" }}>
              <span className="h-[7px] w-[7px] rounded-full" style={{ background: "var(--color-accent)", boxShadow: "0 0 0 3px var(--accent-bg-2)" }} />
              <Avatar label={m.label} roster={roster} size="sm" />
              <span className="min-w-0 truncate">{status === "waiting" ? `${m.label} needs your approval` : `${m.label} is working${row?.now ? ` — ${row.now}` : ""}`}</span>
              {ms !== null && <span className="ml-auto text-[10.5px] tabular-nums text-text-3">{formatElapsed(ms)}</span>}
            </div>
          );
        }
        return (
          <div key={m.label} className="flex items-center gap-2 text-[11.5px] text-text-3">
            <Avatar label={m.label} roster={roster} size="sm" ring="idle" />
            <span>{m.label} is idle</span>
          </div>
        );
      })}
    </div>
  );
}

/** The Board tab (spec 2026-10-05 §6). */
export function Journey({ teamId }: { teamId: string }) {
  const teamsUi = useTeamsUi();
  const [filters, setFilters] = useState<JourneyFilters>(DEFAULT_FILTERS);
  const team = teamsUi.teams[teamId];
  const view = teamsUi.views[teamId];
  const roster = useMemo(() => (team?.members ?? []).map((m) => m.label), [team]);
  const items = useMemo(() => buildJourney(view?.posts ?? [], view?.activity ?? [], roster, filters),
                        [view, roster, filters]);
  const proposals = useMemo(() => new Set((view?.posts ?? []).filter((p) => p.kind === "proposal").map((p) => p.seq)), [view]);
  const isProposal = (seq: number) => proposals.has(seq);
  const toggle = (key: keyof JourneyFilters, name: string) => (
    <button type="button" aria-pressed={filters[key]} onClick={() => setFilters({ ...filters, [key]: !filters[key] })}
      className="h-[22px] rounded-full border px-2.5 text-[11px]"
      style={filters[key]
        ? { color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }
        : { color: "var(--color-text-3)", borderColor: "var(--color-border-strong)" }}>{name}</button>
  );
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-1.5" role="group" aria-label="Show">
        {toggle("posts", "Posts")}{toggle("activity", "Activity")}{toggle("messages", "Messages")}
        <span className="ml-auto text-[10.5px] text-text-3">newest at the bottom</span>
      </div>
      <div className="relative grid gap-2.5 pl-[22px] before:absolute before:bottom-1 before:left-[7px] before:top-1 before:w-[1.5px] before:bg-[var(--color-border-strong)] before:content-['']">
        {items.map((item) => {
          switch (item.kind) {
            case "chapter":
              return (
                <div key={item.key} className="relative -ml-[22px] mb-0.5 mt-1.5 flex items-center gap-2">
                  <span className="grid h-4 w-4 place-items-center rounded-full border-[1.5px]" style={{
                    background: "var(--color-surface-2)",
                    borderColor: item.current ? "var(--color-accent)" : item.ended ? "var(--color-text-3)" : "var(--color-green)" }}>
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: item.current ? "var(--color-accent)" : item.ended ? "var(--color-text-3)" : "var(--color-green)" }} />
                  </span>
                  <h3 className="m-0 text-[11px] font-semibold uppercase tracking-[.08em] text-text-2">{item.title}</h3>
                </div>
              );
            case "post": {
              const { key, ...post } = item;
              return <PostCard key={key} {...post} roster={roster} />;
            }
            case "stance": {
              const { key, ...stance } = item;
              return <StanceReply key={key} {...stance} roster={roster} />;
            }
            case "system":
              return <div key={item.key} data-author="system" className="text-[11px] italic text-text-3">{item.post.text}</div>;
            case "beat": return <Beat key={item.key} event={item.event} isProposal={isProposal} />;
            case "wrap": return <WrapRow key={item.key} event={item.event} roster={roster} />;
            case "round": return <RoundStrip key={item.key} item={item} teamId={teamId} roster={roster} />;
            case "verdict": return <Verdict key={item.key} item={item} roster={roster} />;
            case "adopted": return <AdoptedCard key={item.key} post={item.post} roster={roster} />;
            case "gap":
              return (
                <div key={item.key} className="relative text-center text-[10.5px] text-text-4 before:absolute before:inset-x-0 before:top-1/2 before:border-t before:border-dashed before:border-[var(--color-border-strong)] before:content-['']">
                  <span className="relative px-2" style={{ background: "var(--color-surface-2)" }}>· {item.minutes} min later ·</span>
                </div>
              );
          }
        })}
      </div>
      {team && !isTerminalTeam(team.phase) && <NowStrip teamId={teamId} roster={roster} />}
    </div>
  );
}
