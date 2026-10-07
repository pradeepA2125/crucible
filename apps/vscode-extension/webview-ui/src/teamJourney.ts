import type { TeamActivityView, TeamPostView } from "./types";

// The board as a journey (spec 2026-10-05 §6). Every folding rule lives here, as data;
// components only draw the items.

export interface JourneyFilters { posts: boolean; activity: boolean; messages: boolean }
// Direct messages show by default: the board's #seq references include them, and a hidden
// one read as a missing post.
export const DEFAULT_FILTERS: JourneyFilters = { posts: true, activity: true, messages: true };

export type Stance = "agree" | "object";
export interface TallyChip { label: string; stance: Stance | "pending"; was: Stance | null; seq: number | null }
export interface HeldNote { for: string[]; everyone: boolean; untilRound: number }
export interface PostFooter { woke: string[]; queued: string[]; held: HeldNote | null }
export interface RoundMember { label: string; handed: number[]; reportedAt: string | null; status: string | null }
export interface VerdictProposal { id: string; stances: Record<string, string>; adopted: boolean }

export type JourneyItem =
  | { kind: "chapter"; key: string; title: string; current: boolean; ended: boolean }
  | { kind: "post"; key: string; at: string; post: TeamPostView; footer: PostFooter; tally: TallyChip[] | null }
  | { kind: "stance"; key: string; at: string; post: TeamPostView; replaces: number | null }
  | { kind: "system"; key: string; at: string; post: TeamPostView }
  | { kind: "beat"; key: string; at: string; event: TeamActivityView }
  | { kind: "wrap"; key: string; at: string; event: TeamActivityView }
  | { kind: "round"; key: string; at: string; round: number; members: RoundMember[]; ended: boolean }
  | { kind: "verdict"; key: string; at: string; round: number; proposals: VerdictProposal[];
      adopted: string | null; nextRound: number | null; newPosts: number }
  | { kind: "adopted"; key: string; at: string; post: TeamPostView }
  | { kind: "gap"; key: string; minutes: number };

const GAP_MS = 60_000;
const ENDED_PHASES = new Set(["DONE", "DISBANDED", "FAILED"]);
const STANCE_KINDS = new Set(["agree", "object", "withdraw"]);

export function chapterTitle(phase: string, round: number): string {
  switch (phase) {
    case "DELIBERATING": return `Round ${round} · deliberating`;
    case "AWAITING_APPROVAL": return "Awaiting your approval";
    case "IMPLEMENTING": return "Implementing";
    case "REVIEWING": return "Review";
    case "DEADLOCKED": return "Deadlocked";
    case "PAUSED": return "Paused";
    case "DONE": return "Done";
    case "DISBANDED": return "Disbanded";
    case "FAILED": return "Failed";
    default: return phase.charAt(0) + phase.slice(1).toLowerCase();
  }
}

/** One chip per member (the author excluded): its latest stance on the proposal, and what
 * it was before when that differed. */
export function tallyFor(proposal: TeamPostView, posts: TeamPostView[], roster: string[]): TallyChip[] {
  const id = `P${proposal.seq}`;
  return roster.filter((label) => label !== proposal.author).map((label) => {
    const mine = posts.filter((p) => p.author === label && p.refId === id
                                     && (p.kind === "agree" || p.kind === "object"));
    const last = mine[mine.length - 1];
    const before = last ? [...mine].reverse().find((p) => p.kind !== last.kind) : undefined;
    return {
      label,
      stance: last ? (last.kind as Stance) : "pending",
      was: before ? (before.kind as Stance) : null,
      seq: last ? last.seq : null,
    };
  });
}

const EMPTY_FOOTER = (): PostFooter => ({ woke: [], queued: [], held: null });
const roundOf = (e: TeamActivityView): number | null =>
  typeof e.payload.round === "number" ? e.payload.round : null;

function roundItem(e: TeamActivityView, activity: TeamActivityView[]): Extract<JourneyItem, { kind: "round" }> {
  const round = Number(e.payload.round ?? 0);
  const raw = Array.isArray(e.payload.members) ? e.payload.members as { label?: string; handed?: number[] }[] : [];
  const members = raw.map((m): RoundMember => {
    const label = String(m.label ?? "");
    const wrap = activity.find((x) => x.kind === "wrapped_up" && x.label === label && roundOf(x) === round);
    return { label, handed: Array.isArray(m.handed) ? m.handed : [],
             reportedAt: wrap?.at ?? null, status: wrap ? String(wrap.payload.status ?? "completed") : null };
  });
  const ended = activity.some((x) => x.kind === "round_ended" && roundOf(x) === round);
  return { kind: "round", key: `a${e.aseq}`, at: e.at, round, members, ended };
}

function verdictItem(e: TeamActivityView, activity: TeamActivityView[]): Extract<JourneyItem, { kind: "verdict" }> {
  const round = Number(e.payload.round ?? 0);
  const raw = Array.isArray(e.payload.proposals) ? e.payload.proposals as Record<string, unknown>[] : [];
  const proposals = raw.map((p): VerdictProposal => ({
    id: String(p.id ?? ""), stances: (p.stances as Record<string, string>) ?? {}, adopted: p.adopted === true }));
  const next = activity.some((x) => x.kind === "round_started" && roundOf(x) === round + 1);
  return { kind: "verdict", key: `a${e.aseq}`, at: e.at, round, proposals,
           adopted: typeof e.payload.adopted === "string" ? e.payload.adopted : null,
           nextRound: next ? round + 1 : null, newPosts: Number(e.payload.new_posts ?? 0) };
}

type Entry = { at: number; order: number; post?: TeamPostView; event?: TeamActivityView };

export function buildJourney(
  posts: TeamPostView[], activity: TeamActivityView[], roster: string[],
  filters: JourneyFilters = DEFAULT_FILTERS,
): JourneyItem[] {
  const isCard = (p: TeamPostView) => p.kind !== "system" && !STANCE_KINDS.has(p.kind);
  const cardShown = (p: TeamPostView) =>
    isCard(p) && filters.posts && (p.recipient === null || filters.messages);

  // Rule 3: fold caused wakes into the post that caused them.
  const bySeq = new Map(posts.map((p) => [p.seq, p]));
  const footers = new Map<number, PostFooter>();
  const folded = new Set<number>();
  for (const e of activity) {
    if (e.kind === "took_up" && roundOf(e) !== null) {
      folded.add(e.aseq);                               // the round strip shows what it handed
      continue;
    }
    if (e.kind !== "woke" && e.kind !== "notified" && e.kind !== "held") continue;
    if (e.causeSeq === null) continue;
    const cause = bySeq.get(e.causeSeq);
    if (!cause || !cardShown(cause)) continue;
    const footer = footers.get(cause.seq) ?? EMPTY_FOOTER();
    if (e.kind === "held") {
      footer.held = { for: (e.payload.for as string[]) ?? [], everyone: e.payload.everyone === true,
                      untilRound: Number(e.payload.until_round ?? 0) };
    } else {
      (e.kind === "woke" ? footer.woke : footer.queued).push(e.label);
    }
    footers.set(cause.seq, footer);
    folded.add(e.aseq);
  }

  const items: JourneyItem[] = [{ kind: "chapter", key: "kickoff", title: "Kickoff", current: false, ended: false }];
  let lastAt: number | null = null;
  const timed = (item: Exclude<JourneyItem, { kind: "chapter" } | { kind: "gap" }>) => {
    const at = Date.parse(item.at);
    if (lastAt !== null && at - lastAt > GAP_MS) {
      items.push({ kind: "gap", key: `gap-${item.key}`, minutes: Math.round((at - lastAt) / 60_000) });
    }
    lastAt = at;
    items.push(item);
  };
  const postItem = (p: TeamPostView): void => {
    if (p.kind === "system") {
      timed(typeof p.payload.adopted === "string"
        ? { kind: "adopted", key: `p${p.seq}`, at: p.createdAt, post: p }
        : { kind: "system", key: `p${p.seq}`, at: p.createdAt, post: p });
    } else if (STANCE_KINDS.has(p.kind)) {
      if (!filters.posts) return;
      const earlier = posts.filter((q) => q.seq < p.seq && q.author === p.author
                                         && q.refId === p.refId && STANCE_KINDS.has(q.kind));
      timed({ kind: "stance", key: `p${p.seq}`, at: p.createdAt, post: p,
              replaces: earlier.length ? earlier[earlier.length - 1].seq : null });
    } else if (cardShown(p)) {
      timed({ kind: "post", key: `p${p.seq}`, at: p.createdAt, post: p,
              footer: footers.get(p.seq) ?? EMPTY_FOOTER(),
              tally: p.kind === "proposal" ? tallyFor(p, posts, roster) : null });
    }
  };

  // Rule 1: the kickoff first, then everything else in time order.
  const kickoff = posts.filter((p) => p.round === 0);
  kickoff.forEach(postItem);
  const entries: Entry[] = [
    ...posts.filter((p) => p.round !== 0).map((p) => ({ at: Date.parse(p.createdAt), order: 0, post: p })),
    ...activity.map((e) => ({ at: Date.parse(e.at), order: 1, event: e })),
  ].sort((a, b) => a.at - b.at || a.order - b.order);

  for (const entry of entries) {
    if (entry.post) {
      postItem(entry.post);
      continue;
    }
    const e = entry.event as TeamActivityView;
    if (e.kind === "phase") {
      const phase = String(e.payload.phase ?? "");
      items.push({ kind: "chapter", key: `a${e.aseq}`,
                   title: chapterTitle(phase, Number(e.payload.round ?? 1)),
                   current: false, ended: ENDED_PHASES.has(phase) });
      continue;
    }
    if (e.kind === "round_started") {
      timed(roundItem(e, activity));
      continue;
    }
    if (e.kind === "round_ended") {
      timed(verdictItem(e, activity));
      continue;
    }
    if (!filters.activity || folded.has(e.aseq)) continue;
    timed(e.kind === "wrapped_up"
      ? { kind: "wrap", key: `a${e.aseq}`, at: e.at, event: e }
      : { kind: "beat", key: `a${e.aseq}`, at: e.at, event: e });
  }

  // Rule 2: the last chapter is the current one, unless the team has ended.
  const chapters = items.filter((i): i is Extract<JourneyItem, { kind: "chapter" }> => i.kind === "chapter");
  const last = chapters[chapters.length - 1];
  if (!last.ended) last.current = true;
  return items;
}
