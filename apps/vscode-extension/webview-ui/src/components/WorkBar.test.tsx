import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { WorkBar } from "./WorkBar";
import { STATUS_WORDS, STALL_THRESHOLD_SEC, formatTokens } from "../statusWords";
import type { TokenProgressView } from "../types";

const BASE = {
  workbar: null,
  liveStatus: null,
  thinkingStatus: null,
} as const;

/** Advance the WorkBar's 1s interval by `secs` inside act(). */
function tick(secs: number) {
  act(() => {
    vi.advanceTimersByTime(secs * 1000);
  });
}

describe("WorkBar waiting state", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("rotates a status word instead of freezing on a static label", () => {
    // The prefill window produces no deltas, no thinking and no phase label, so the
    // bar sat on a motionless "Working…" beside a spinning dot — for minutes on a
    // large prompt, which reads as a hung process.
    render(<WorkBar {...BASE} visible tokenProgress={null} />);

    const shown = new Set<string>();
    for (let i = 0; i < 5; i++) {
      const match = STATUS_WORDS.find((w) => screen.queryByText(`${w}…`));
      if (match) shown.add(match);
      tick(4);
    }
    expect(shown.size).toBeGreaterThan(1);
    expect(screen.queryByText("Working…")).toBeNull();
  });

  it("yields to real signal the moment there is one — whimsy never competes", () => {
    render(
      <WorkBar {...BASE} thinkingStatus="Reading auth.py" visible tokenProgress={null} />,
    );
    tick(12);
    expect(screen.getByText("Reading auth.py")).toBeTruthy();
    for (const w of STATUS_WORDS) expect(screen.queryByText(`${w}…`)).toBeNull();
  });

  it("escalates past the stall threshold, because a spinner lies during a wedge", () => {
    // A rotating word and a spinning dot are both client-side; they animate happily
    // through a wedged turn. This is the only signal tied to the backend actually
    // producing something.
    const progress: TokenProgressView = {
      thinking: 0,
      output: 0,
      input: 372_000,
      exact: false,
    };
    render(<WorkBar {...BASE} visible tokenProgress={progress} />);

    tick(STALL_THRESHOLD_SEC - 5);
    expect(screen.queryByText(/Still nothing/)).toBeNull();

    tick(10);
    const stall = screen.getByText(/Still nothing after/);
    expect(stall.textContent).toContain(formatTokens(372_000));
  });
});

describe("WorkBar token counter", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("marks estimates with ~ and shows the exact figure plain", () => {
    // Live ticks are chars/4 guesses; only the closing tick carries provider usage.
    // Rendering them identically meant a moving number and a settled one looked the
    // same, so "is that final?" had no answer.
    const { rerender } = render(
      <WorkBar
        {...BASE}
        visible
        tokenProgress={{ thinking: 100, output: 2000, input: 5000, exact: false }}
      />,
    );
    expect(screen.getByText(/~2,000/)).toBeTruthy();

    rerender(
      <WorkBar
        {...BASE}
        visible
        tokenProgress={{ thinking: 100, output: 2048, input: 5000, exact: true }}
      />,
    );
    expect(screen.getByText(/2,048/)).toBeTruthy();
    expect(screen.queryByText(/~2,048/)).toBeNull();
  });

  it("shows input size alone during prefill, when it is the only number that exists", () => {
    render(
      <WorkBar
        {...BASE}
        visible
        tokenProgress={{ thinking: 0, output: 0, input: 372_000, exact: false }}
      />,
    );
    expect(screen.getByText(`↑ ${formatTokens(372_000)}`)).toBeTruthy();
  });

  it("keeps the final count readable after the turn ends", () => {
    // visible=false means the turn is over. The exact count must survive, or it
    // flashes and vanishes at the exact moment it becomes correct.
    render(
      <WorkBar
        {...BASE}
        visible={false}
        tokenProgress={{ thinking: 10, output: 4096, input: 5000, exact: true }}
      />,
    );
    expect(screen.getByText(/4,096/)).toBeTruthy();
  });

  it("renders nothing when the turn is over and there is no count to show", () => {
    const { container } = render(
      <WorkBar {...BASE} visible={false} tokenProgress={null} />,
    );
    expect(container.firstChild).toBeNull();
  });
});

describe("formatTokens", () => {
  it("keeps a bar's numbers in one consistent shape", () => {
    expect(formatTokens(999)).toBe("999");
    expect(formatTokens(1200)).toBe("1.2k");
    expect(formatTokens(372_000)).toBe("372k");
    expect(formatTokens(1_500_000)).toBe("1.5M");
  });
});

describe("WorkBar stall detection across providers", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("never claims a stall when the provider reports no token progress at all", () => {
    // Only openai_compatible (and openrouter by inheritance) set
    // supports_token_progress; the other eight transports never pass on_progress, so
    // tokenProgress stays null for the whole turn. Treating "no signal" as "no
    // progress" would fire the stall warning on EVERY Ollama/Gemini/Anthropic turn
    // past the threshold — the exact false alarm this escalation must not produce.
    // Absence of evidence is not evidence of a wedge.
    render(<WorkBar {...BASE} visible tokenProgress={null} />);
    tick(STALL_THRESHOLD_SEC + 60);
    expect(screen.queryByText(/Still nothing/)).toBeNull();
  });

  it("still shows the rotating word on providers with no counter", () => {
    render(<WorkBar {...BASE} visible tokenProgress={null} />);
    tick(STALL_THRESHOLD_SEC + 60);
    const anyWord = STATUS_WORDS.some((w) => screen.queryByText(`${w}…`));
    expect(anyWord).toBe(true);
  });
});
