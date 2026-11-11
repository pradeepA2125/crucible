import { describe, expect, it } from "vitest";
import { EchoGate } from "./echoGate";

describe("EchoGate: the user's voice, not the reply's own sound", () => {
  it("echo alone never counts as the user", () => {
    const gate = new EchoGate(); // laptop speakers: mic hears 30% of output, Silero says speech
    for (let i = 0; i < 500; i += 1) expect(gate.observe(0.3 * 0.1, 0.1, 0.95)).toBe(false);
  });

  it("a syllable-shaped echo never counts as the user", () => {
    // One reply chunk reports one output level, but its echo rises and falls with each
    // syllable: loud for 80 ms, quiet between. The gaps must not set the echo path.
    const gate = new EchoGate();
    for (let i = 0; i < 400; i += 1) {
      const syllable = Math.floor(i / 4) % 2 === 0;
      expect(gate.observe(syllable ? 0.06 : 0.004, 0.1, 0.95)).toBe(false);
    }
  });

  it("echo still arriving after its reply chunk ended is not the user", () => {
    const gate = new EchoGate();
    for (let i = 0; i < 100; i += 1) gate.observe(0.03, 0.1, 0.95);
    // The output level drops to zero at a chunk boundary while the room still carries
    // the last syllable (and the frame was captured before the drop was measured).
    for (let i = 0; i < 8; i += 1) expect(gate.observe(0.03, 0, 0.95)).toBe(false);
  });

  it("once the reply has been quiet a while, speech counts from the probability", () => {
    const gate = new EchoGate();
    for (let i = 0; i < 100; i += 1) gate.observe(0.03, 0.1, 0.95);
    for (let i = 0; i < 20; i += 1) gate.observe(0.001, 0, 0.1);
    expect(Array.from({ length: 4 }, () => gate.observe(0.05, 0, 0.9)).at(-1)).toBe(true);
  });

  it("the user's voice over the echo is recognised within 80 ms", () => {
    const gate = new EchoGate();
    for (let i = 0; i < 150; i += 1) gate.observe(0.03, 0.1, 0.95);
    const hits = Array.from({ length: 4 }, () => gate.observe(0.03 + 0.08, 0.1, 0.95));
    expect(hits).toEqual([false, false, false, true]);
  });

  it("with headphones (no echo) speech is recognised from the probability", () => {
    const gate = new EchoGate();
    expect(Array.from({ length: 4 }, () => gate.observe(0.05, 0, 0.9)).at(-1)).toBe(true);
  });

  it("the user's own speech never teaches it the echo level", () => {
    const gate = new EchoGate(); // the user talks from the first frame of a reply
    const results = Array.from({ length: 60 }, () => gate.observe(0.4, 0.1, 0.9));
    expect(results.slice(3)).toEqual(Array(57).fill(true));
  });

  it("a loud non-voice sound is not the user", () => {
    const gate = new EchoGate();
    for (let i = 0; i < 20; i += 1) expect(gate.observe(0.2, 0.1, 0.1)).toBe(false);
  });

  it("reset forgets the established voice", () => {
    const gate = new EchoGate();
    for (let i = 0; i < 4; i += 1) gate.observe(0.05, 0, 0.9);
    gate.reset();
    expect(gate.observe(0.05, 0, 0.9)).toBe(false);
  });
});
