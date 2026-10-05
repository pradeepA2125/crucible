import { elapsedMs, formatElapsed, isTerminalAgent } from "../../agents";
import { identityFor } from "../../teamIdentity";
import type { HeldNote, JourneyItem } from "../../teamJourney";
import type { TeamPostView } from "../../types";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { Avatar } from "./Avatar";
import { useTeamsUi } from "./TeamsContext";

// The deliberation's structure (spec 2026-10-05 §9; mockup .round / .verdict / .sys).

const clock = (at: string) => new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function Name({ label, roster }: { label: string; roster: string[] }) {
  return <span className="font-semibold" style={{ color: identityFor(label, roster).color }}>{label}</span>;
}

export function heldText(held: HeldNote): string {
  return held.everyone
    ? `for everyone · held for round ${held.untilRound}`
    : `for ${held.for.join(" · ")} · held for round ${held.untilRound} — nothing reaches a member mid-round`;
}

function wokeText(count: number, total: number, labels: string[]): string {
  if (count === total && total === 2) return "both members woke";
  if (count === total) return `all ${total} members woke`;
  return `${labels.join(", ")} woke`;
}

export function RoundStrip({ item, teamId, roster }: {
  item: Extract<JourneyItem, { kind: "round" }>; teamId: string; roster: string[];
}) {
  const agentsUi = useAgentsUi();
  const team = useTeamsUi().teams[teamId];
  const rowFor = (label: string) => {
    const member = team?.members.find((m) => m.label === label);
    return member ? agentsUi.agents[member.agentId] : undefined;
  };
  const pending = item.members.filter((m) => m.reportedAt === null);
  const live = !item.ended && pending.some((m) => { const r = rowFor(m.label); return r && !isTerminalAgent(r.status); });
  const now = useNow(live);
  const reported = item.members.length - pending.length;
  const waiting = pending.find((m) => { const r = rowFor(m.label); return r && !isTerminalAgent(r.status); });
  const waitingRow = waiting ? rowFor(waiting.label) : undefined;
  const pct = item.members.length ? Math.round((reported / item.members.length) * 100) : 0;
  return (
    <div data-testid={`round-${item.round}`} className="relative grid gap-1.5 rounded-[10px] border px-3 py-2.5"
      style={{ borderColor: "var(--accent-brd)", background: "linear-gradient(180deg, var(--accent-bg), transparent 70%), var(--color-surface)" }}>
      <span aria-hidden="true" className="absolute left-[-22px] top-2 grid h-4 w-4 place-items-center rounded-full border-[1.5px] text-[10px]"
        style={{ background: "var(--color-surface-2)", borderColor: "var(--color-amber)", color: "var(--color-amber)" }}>⚡</span>
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <strong className="font-semibold">Round {item.round} started — {wokeText(item.members.length, roster.length, item.members.map((m) => m.label))}</strong>
        <span className="ml-auto text-[10.5px] tabular-nums text-text-3">{clock(item.at)}</span>
      </div>
      <div className="grid gap-1">
        {item.members.map((m) => {
          const r = rowFor(m.label);
          const working = m.reportedAt === null && r !== undefined && !isTerminalAgent(r.status);
          return (
            <div key={m.label} className="grid grid-cols-[auto_52px_minmax(0,1fr)_auto] items-center gap-2 text-[11.5px] text-text-2">
              <Avatar label={m.label} roster={roster} size="sm" />
              <Name label={m.label} roster={roster} />
              <span className="flex flex-wrap items-center gap-1">
                {m.handed.map((s) => <span key={s} className="font-mono text-[10.5px] font-semibold text-text-3">#{s}</span>)}
                <span className="text-text-3">{m.handed.length ? `${m.handed.length} post${m.handed.length === 1 ? "" : "s"}` : "nothing new"}</span>
              </span>
              <span className="whitespace-nowrap text-[10.5px]" style={{ color: m.reportedAt ? "var(--color-green)" : "var(--color-accent-ink)" }}>
                {m.reportedAt ? `✓ reported ${clock(m.reportedAt)}` : working && r ? `● working · ${formatElapsed(elapsedMs(r, now) ?? 0)}` : "waiting"}
              </span>
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-2.5 text-[11px] text-text-2">
        <span>{reported} of {item.members.length} reported{!item.ended && waiting && waitingRow
          ? ` · waiting on ${waiting.label} (${formatElapsed(elapsedMs(waitingRow, now) ?? 0)})` : ""}</span>
        <span className="h-[5px] max-w-[200px] flex-1 overflow-hidden rounded-full" style={{ background: "var(--color-surface-2)" }}>
          <i className="block h-full rounded-full" style={{ width: `${pct}%`, background: item.ended ? "var(--color-green)" : "var(--color-accent)" }} />
        </span>
      </div>
    </div>
  );
}

const MARK: Record<string, string> = { agree: "✓", object: "✗", none: "–" };

export function Verdict({ item, roster }: { item: Extract<JourneyItem, { kind: "verdict" }>; roster: string[] }) {
  const yes = item.adopted !== null;
  return (
    <div data-testid={`verdict-${item.round}`} className="relative flex flex-wrap items-center gap-2 rounded-lg px-2.5 py-1.5 text-[11.5px]"
      style={yes
        ? { border: "1px solid var(--green-brd)", background: "var(--green-bg)", color: "var(--color-text)" }
        : { border: "1px dashed var(--color-border-strong)", color: "var(--color-text-2)" }}>
      <span aria-hidden="true" className="absolute left-[-19px] top-1/2 -mt-[4.5px] h-[9px] w-[9px] rotate-45"
        style={{ background: yes ? "var(--color-green)" : "var(--color-amber)", boxShadow: "0 0 0 3px var(--color-surface-2)" }} />
      {/* Spaces are explicit text: the row is flex, and the line must read as one sentence. */}
      <strong>Round {item.round} ended</strong>
      {item.proposals.length === 0 && <span>{" · no open proposals"}</span>}
      {item.proposals.map((p) => (
        <span key={p.id}>
          {` · ${p.id}: `}
          {Object.entries(p.stances).map(([label, stance], i) => (
            <span key={label}>{i > 0 && " "}<Name label={label} roster={roster} />{` ${MARK[stance] ?? "–"}`}</span>
          ))}
          {" — "}{p.adopted ? <strong style={{ color: "var(--color-green)" }}>adopted</strong> : "not adopted"}
        </span>
      ))}
      {!yes && item.nextRound !== null && (
        <span className="text-text-3">{` · round ${item.nextRound} next, with ${item.newPosts} new post${item.newPosts === 1 ? "" : "s"}`}</span>
      )}
      {!yes && item.nextRound === null && <span className="text-text-3">{" · the round limit is reached"}</span>}
    </div>
  );
}

export function AdoptedCard({ post, roster }: { post: TeamPostView; roster: string[] }) {
  const assignments = Array.isArray(post.payload.assignments)
    ? post.payload.assignments as { member?: string; part?: string; files?: string[] }[] : [];
  const shared = Array.isArray(post.payload.shared_files) ? post.payload.shared_files as string[] : [];
  return (
    <div data-testid={`adopted-p${post.seq}`} className="relative grid gap-1 rounded-[10px] border border-border px-3 py-2 text-[12px]"
      style={{ background: "var(--color-panel)" }}>
      <span aria-hidden="true" className="absolute left-[-19px] top-[13px] h-[9px] w-[9px] rounded-full"
        style={{ background: "var(--color-green)", boxShadow: "0 0 0 3px var(--color-surface-2)" }} />
      <div><strong>Adopted {String(post.payload.adopted)}{post.payload.by === "main" ? " by the main agent" : ""}.</strong></div>
      {assignments.map((a, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5 text-text-2">
          <Avatar label={String(a.member)} roster={roster} size="sm" />
          <Name label={String(a.member)} roster={roster} /> → {a.part}
          {(a.files ?? []).map((f) => <code key={f} className="font-mono text-[11px] text-[var(--color-code)]">{f}</code>)}
        </div>
      ))}
      {shared.length > 0 && <div className="text-[11px] text-text-3">shared: {shared.join(", ")}</div>}
    </div>
  );
}
