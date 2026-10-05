import type { ChatMsg, TeamActivityView, TeamPostView } from "./types";

// A member's own journey (spec 2026-10-05 §7): one chapter per activation.

export interface ChapterWhy { cause: string; by: string | null; postSeq: number | null; round?: number | null }

export interface MemberChapter {
  n: number;
  why: ChapterWhy | null;
  start: string | null;
  status: string;
  durationMs: number | null;
  handed: number[];
  pickedUp: { posts: number[]; at: string }[];
  work: ChatMsg[];          // the chapter's transcript messages, report excluded
  tools: number;
  report: string | null;
  said: TeamPostView[];     // the member's posts during the chapter
  current: boolean;
}

export function countTools(messages: ChatMsg[]): number {
  return messages.reduce((n, m) => {
    const events = m.metadata?.tool_events;
    return n + (Array.isArray(events) ? events.length : 0);
  }, 0);
}

const seqs = (v: unknown): number[] => (Array.isArray(v) ? v.filter((x): x is number => typeof x === "number") : []);

export function buildChapters(
  label: string, messages: ChatMsg[], activity: TeamActivityView[], posts: TeamPostView[],
  opts: { working: boolean; fallbackStatus: string },
): MemberChapter[] {
  const segments: { n: number; messages: ChatMsg[] }[] = [{ n: 1, messages: [] }];
  for (const m of messages) {
    if (m.metadata?.divider === true) {
      const n = typeof m.metadata.activation === "number" ? m.metadata.activation : segments.length + 1;
      segments.push({ n, messages: [] });
      continue;
    }
    segments[segments.length - 1].messages.push(m);
  }
  const mine = activity.filter((e) => e.label === label);
  const chapters = segments.map((seg, i): MemberChapter => {
    const events = mine.filter((e) => e.activation === seg.n);
    const woke = events.find((e) => e.kind === "woke");
    const took = events.find((e) => e.kind === "took_up");
    const wrap = events.find((e) => e.kind === "wrapped_up");
    const reportMsg = seg.messages.find((m) => m.metadata?.report === true);
    const wrapReport = typeof wrap?.payload.report === "string" && wrap.payload.report ? wrap.payload.report : null;
    const last = i === segments.length - 1;
    const current = last && opts.working;
    return {
      n: seg.n,
      why: woke ? { cause: String(woke.payload.cause ?? ""), by: (woke.payload.by as string | null) ?? null,
                    postSeq: (woke.payload.post_seq as number | null) ?? woke.causeSeq }
        : typeof took?.payload.round === "number"
          ? { cause: "round", by: null, postSeq: null, round: took.payload.round }
          : null,
      start: took?.at ?? woke?.at ?? null,
      status: wrap ? String(wrap.payload.status ?? "completed") : current ? "working" : last ? opts.fallbackStatus : "completed",
      durationMs: typeof wrap?.payload.duration_ms === "number" ? wrap.payload.duration_ms : null,
      handed: seqs(took?.payload.posts),
      pickedUp: events.filter((e) => e.kind === "picked_up").map((e) => ({ posts: seqs(e.payload.posts), at: e.at })),
      work: seg.messages.filter((m) => m.metadata?.report !== true),
      tools: countTools(seg.messages),
      report: reportMsg ? reportMsg.content : wrapReport,
      said: [],
      current,
    };
  });
  // Assign the member's posts by time: a chapter holds what was written from its start
  // until the next chapter's start (a chapter with no start opens at the beginning).
  const authored = posts.filter((p) => p.author === label);
  chapters.forEach((c, i) => {
    const from = c.start ? Date.parse(c.start) : Number.NEGATIVE_INFINITY;
    const next = chapters[i + 1]?.start;
    const to = next ? Date.parse(next) : Number.POSITIVE_INFINITY;
    c.said = authored.filter((p) => {
      const at = Date.parse(p.createdAt);
      return at >= from && at < to;
    });
  });
  return chapters;
}

export function whyText(why: ChapterWhy | null, isProposal: (seq: number) => boolean): string {
  if (!why) return "started";
  const ref = why.postSeq === null ? "" : isProposal(why.postSeq) ? `P${why.postSeq}` : `#${why.postSeq}`;
  switch (why.cause) {
    case "kickoff": return `kickoff — main's proposal ${ref}`.trim();
    case "mention": return `woken by ${why.by}'s post ${ref}`;
    case "team_mention": return `woken by ${why.by}'s @team post ${ref}`;
    case "message": return `woken by ${why.by}'s message ${ref}`;
    case "main_post": return `woken by main's post ${ref}`;
    case "leftover": return "leftover input";
    default: return why.cause;
  }
}

/** A chapter's header: deliberation activations are rounds (spec 2026-10-05 §9). */
export function chapterHeading(c: MemberChapter, isProposal: (seq: number) => boolean): { title: string; why: string } {
  if (c.why?.cause === "round") {
    const handed = c.handed.map((s) => (isProposal(s) ? `P${s}` : `#${s}`)).join(" ");
    return { title: `Round ${c.why.round}`, why: handed ? `handed ${handed}` : "nothing new on the board" };
  }
  return { title: `Chapter ${c.n}`, why: whyText(c.why, isProposal) };
}
