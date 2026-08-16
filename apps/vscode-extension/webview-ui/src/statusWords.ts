/**
 * Whimsical status words for the WorkBar's generic waiting state.
 *
 * These fill exactly one gap: the window before ANY real signal exists. During
 * prefill the model has produced no reasoning, no phase label and no step, so the
 * bar previously showed a frozen "Working…" beside a spinning dot — for minutes on
 * a large prompt. A static label next to a moving spinner reads as a hung process.
 *
 * They are decoration, NOT information. The moment a real signal arrives (thinking
 * text, a phase label, a step) the caller stops rotating and shows that instead —
 * whimsy must never compete with something true. And because the rotation is
 * client-side it proves only that the webview is alive, never that the backend is;
 * that is what the stall escalation is for.
 */

/** Deliberately mundane-but-odd. Nothing that implies progress we can't observe
 * ("Almost there…" would be a lie), and nothing that implies an error. */
export const STATUS_WORDS: readonly string[] = [
  "Meandering",
  "Percolating",
  "Ruminating",
  "Noodling",
  "Marinating",
  "Untangling",
  "Rummaging",
  "Pondering",
  "Wrangling",
  "Simmering",
  "Deliberating",
  "Tinkering",
  "Puzzling",
  "Cogitating",
  "Whittling",
  "Mulling",
  "Scheming",
  "Brewing",
  "Fossicking",
  "Woolgathering",
];

/**
 * The word for a given tick, starting from a per-turn seed.
 *
 * Seeded rather than random-per-call so a re-render mid-tick doesn't reshuffle the
 * word (which would read as frantic), and offset per turn so consecutive turns
 * don't always open on "Meandering".
 */
export function statusWordAt(seed: number, tick: number): string {
  const i = Math.abs(Math.trunc(seed) + Math.trunc(tick)) % STATUS_WORDS.length;
  return STATUS_WORDS[i];
}

/**
 * Seconds without a single token before the bar stops being playful and says
 * something is wrong.
 *
 * 90s is chosen to sit above a legitimately slow prefill (measured: ~2min calls on
 * a 372k-token prompt did eventually return) while still being far below the
 * multi-hour livelock this exists to surface. It is a floor on suspicion, not a
 * timeout — nothing is cancelled, the user is just told to look.
 */
export const STALL_THRESHOLD_SEC = 90;

/** Escalation copy. Deliberately factual and slightly apologetic — it appears when
 * the playful label would otherwise be actively misleading. */
export function stallMessage(silentSec: number, inputTokens: number | null): string {
  const mins = Math.floor(silentSec / 60);
  const howLong = mins >= 1 ? `${mins}m` : `${silentSec}s`;
  return inputTokens
    ? `Still nothing after ${howLong} — prefilling ~${formatTokens(inputTokens)} tokens`
    : `Still nothing after ${howLong} — the model hasn't sent a token yet`;
}

/** 1234 → "1.2k", 372000 → "372k". Shared by the counter and the stall copy so a
 * number never appears in two different shapes in the same bar. */
export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) {
    const k = n / 1000;
    return `${k >= 100 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, "")}k`;
  }
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}
