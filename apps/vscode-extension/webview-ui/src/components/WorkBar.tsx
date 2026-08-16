import { useState, useEffect, useMemo, useRef } from "react";
import type { WorkbarInfo, TokenProgressView } from "../types";
import {
  statusWordAt,
  stallMessage,
  formatTokens,
  STALL_THRESHOLD_SEC,
} from "../statusWords";

// Status-to-label map (tier 3 of the label precedence hierarchy).
const STATUS_LABELS: Record<string, string> = {
  QUEUED: "Queued…",
  CONTEXT_READY: "Planning — exploring the codebase…",
  PLANNED: "Generating execution plan…",
  EXECUTING: "Executing…",
  VALIDATING: "Running validation…",
  REPAIRING: "Repairing validation errors…",
  PROMOTING: "Applying changes…",
};

interface Props {
  workbar: WorkbarInfo | null;
  liveStatus: string | null;
  thinkingStatus: string | null;
  tokenProgress?: TokenProgressView | null;
  visible: boolean;
}

function formatElapsed(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Thousands separators — a five-digit raw count is hard to read at a glance. */
function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}

/**
 * Live token counter: 🧠 while reasoning, ↓ once output starts.
 *
 * The output glyph is a text-presentation arrow rather than an emoji so it
 * actually inherits its colour token — an emoji renders in its own palette and
 * ignores `color`, which is what made the previous ✍ read as muddy at 10px.
 *
 * The two phases are shown separately because they mean different things to
 * someone waiting: reasoning streams visibly into the thinking pane, but content
 * is accumulated silently until the call returns — so without this, the moment
 * reasoning ends looks exactly like a hang. Counts are delta tallies (measured
 * ~1.4% below true token counts), which is why this reads as an activity
 * indicator rather than a billing figure.
 */
function TokenCounter({ progress }: { progress: TokenProgressView }) {
  const { thinking, output, input, exact } = progress;
  // `input` alone is worth rendering: during prefill it is the ONLY number that
  // exists, and it is precisely the window that used to look like a hang.
  if (!thinking && !output && !input) return null;

  // Estimated counts are marked with `~` and dimmed; the exact closing figure is
  // shown plain and solid. Without this the live guess and the provider's real
  // usage were visually identical, so a moving number and a settled one read the
  // same — you could not tell whether what you were looking at was final.
  const tilde = exact ? "" : "~";
  const dim = exact ? "var(--color-text-3)" : "var(--color-text-4)";
  const suffix = exact ? "reported by provider" : "approximate, still streaming";

  return (
    <span
      className="flex-shrink-0 flex items-center gap-2 font-mono tabular-nums"
      style={{ fontSize: "10px", color: dim }}
      aria-label={
        `${input ?? 0} input tokens, ${thinking} thinking tokens, ` +
        `${output} output tokens (${exact ? "exact" : "estimated"})`
      }
    >
      {input ? (
        <span title={`prompt size (${suffix})`}>↑ {formatTokens(input)}</span>
      ) : null}
      {thinking > 0 && (
        <span title={`reasoning tokens (${suffix})`}>
          🧠 {tilde}
          {formatCount(thinking)}
        </span>
      )}
      {output > 0 && (
        <span
          title={`output tokens (${suffix})`}
          style={{ color: "var(--color-green)" }}
        >
          ↓ {tilde}
          {formatCount(output)}
        </span>
      )}
    </span>
  );
}

/**
 * WorkBar — spinner + label + elapsed timer.
 *
 * Visible while the task is actively running (not waiting on user).
 * Stop affordance intentionally omitted — InputArea owns the Stop button.
 *
 * Label precedence (F14 three tiers):
 *   1. stepIndex + totalSteps → "Step {i} of {n} — {stepTitle}"
 *   2. workbar.phaseLabel
 *   3. STATUS_LABELS map keyed by liveStatus
 *   4. fallback: thinkingStatus ?? "Working…"
 */
export function WorkBar({ workbar, liveStatus, thinkingStatus, tokenProgress, visible }: Props) {
  const [elapsed, setElapsed] = useState(0);

  // Reset timer when bar becomes visible; count while visible.
  useEffect(() => {
    if (!visible) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [visible]);

  // Per-turn seed so consecutive turns don't always open on the same word.
  const seed = useMemo(() => Math.floor(Math.random() * 1000), [visible]);

  // Seconds since the token counter last moved. This is the ONLY liveness signal
  // tied to the backend rather than to the webview's own clock: a rotating word and
  // a spinning dot both keep animating happily through a wedged turn, which is
  // exactly how a 4-hour livelock went unnoticed.
  const lastProgressRef = useRef(0);
  const progressKey = tokenProgress
    ? `${tokenProgress.input}:${tokenProgress.thinking}:${tokenProgress.output}`
    : "none";
  useEffect(() => {
    lastProgressRef.current = elapsed;
    // `elapsed` intentionally omitted: this must run when progress CHANGES, not
    // every tick, or the stall timer could never accumulate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progressKey]);
  const silentFor = elapsed - lastProgressRef.current;
  // Gated on tokenProgress EXISTING, not just on it being stale. Only
  // openai_compatible (and openrouter, by inheritance) set supports_token_progress —
  // the other eight transports never pass on_progress, so tokenProgress stays null
  // for the entire turn. Without this gate the stall would fire on every Ollama,
  // Gemini or Anthropic turn past the threshold, which is a false alarm, not a
  // diagnosis: absence of a signal is not evidence of a wedge. On those providers
  // the bar keeps rotating and simply never escalates.
  const stalled =
    visible && tokenProgress != null && silentFor >= STALL_THRESHOLD_SEC;

  if (!visible && !tokenProgress) return null;

  // The turn is over but a final count survives (the host clears it at the next
  // turn's start). Render a static, spinner-free summary so the one exact number
  // the provider gave us stays readable instead of vanishing the instant it lands.
  if (!visible && tokenProgress) {
    return (
      <div
        className="relative flex items-center gap-2 px-3 py-1 flex-shrink-0"
        style={{ borderTop: "1px solid var(--color-border)", background: "var(--color-surface)" }}
      >
        <span className="flex-1 min-w-0" />
        <TokenCounter progress={tokenProgress} />
      </div>
    );
  }

  // Compute label (tier 1 → 2 → 3 → 4).
  let label: React.ReactNode;
  if (
    workbar?.stepIndex !== undefined &&
    workbar?.stepIndex !== null &&
    workbar?.totalSteps !== undefined &&
    workbar?.totalSteps !== null
  ) {
    label = (
      <>
        <span
          className="font-semibold"
          style={{ color: "var(--color-accent-ink)" }}
        >
          Step {workbar.stepIndex} of {workbar.totalSteps}
        </span>
        {workbar.stepTitle ? (
          <span style={{ color: "var(--color-text-2)" }}>
            {" — "}
            {workbar.stepTitle}
          </span>
        ) : null}
      </>
    );
  } else if (workbar?.phaseLabel) {
    label = (
      <span style={{ color: "var(--color-text-2)" }}>{workbar.phaseLabel}</span>
    );
  } else if (liveStatus && STATUS_LABELS[liveStatus]) {
    label = (
      <span style={{ color: "var(--color-text-2)" }}>
        {STATUS_LABELS[liveStatus]}
      </span>
    );
  } else if (thinkingStatus) {
    label = <span style={{ color: "var(--color-text-2)" }}>{thinkingStatus}</span>;
  } else {
    // Tier 4 — nothing real to report yet. This is the prefill window, and it used
    // to render a frozen "Working…" beside a spinning dot for minutes on a large
    // prompt, which reads as a hung process. Rotate a word every ~4s so the bar is
    // visibly alive. The instant ANY real signal arrives one of the branches above
    // wins and rotation stops — decoration never competes with information.
    label = (
      <span style={{ color: "var(--color-text-2)" }}>
        {statusWordAt(seed, Math.floor(elapsed / 4))}…
      </span>
    );
  }

  // A stall outranks every label above. Past the threshold the playful word is not
  // merely uninformative, it is actively misleading — it implies healthy progress
  // during exactly the silence the user needs to notice.
  if (stalled) {
    label = (
      <span style={{ color: "var(--color-amber, var(--color-text-2))" }}>
        {stallMessage(silentFor, tokenProgress?.input ?? null)}
      </span>
    );
  }

  return (
    <div
      className="relative flex items-center gap-2 px-3 py-2 flex-shrink-0"
      style={{
        borderTop: "1px solid var(--color-border)",
        background: "var(--color-surface)",
      }}
    >
      {/* Animated top hairline */}
      <span className="workbar-line" aria-hidden="true" />

      {/* Spinner */}
      <span
        className="flex-shrink-0 rounded-full border-2"
        style={{
          width: 9,
          height: 9,
          borderColor: "var(--color-accent-ink) var(--accent-bg) var(--accent-bg) var(--accent-bg)",
          animation: "spin 0.75s linear infinite",
        }}
        aria-hidden="true"
      />

      {/* Label — takes remaining space, truncated */}
      <span
        className="flex-1 min-w-0 truncate text-[11px]"
        aria-live="polite"
      >
        {label}
      </span>

      {/* Live token counter — between label and timer */}
      {tokenProgress ? <TokenCounter progress={tokenProgress} /> : null}

      {/* Elapsed timer */}
      <span
        className="flex-shrink-0 font-mono tabular-nums"
        style={{ fontSize: "10px", color: "var(--color-text-4)" }}
        aria-label={`Elapsed ${formatElapsed(elapsed)}`}
      >
        {formatElapsed(elapsed)}
      </span>
    </div>
  );
}
