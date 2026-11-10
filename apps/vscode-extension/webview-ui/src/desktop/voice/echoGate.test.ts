import { describe, expect, it } from "vitest";
import { EchoGate } from "./echoGate";

describe("EchoGate: the user's voice, not the reply's own sound", () => {
  it("echo alone never counts as the user", () => {
    const gate = new EchoGate(); // laptop speakers: mic hears 30% of output, Silero says speech
    for (let i = 0; i < 500; i += 1) expect(gate.observe(0.3 * 0.1, 0.1, 0.95)).toBe(false);
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
