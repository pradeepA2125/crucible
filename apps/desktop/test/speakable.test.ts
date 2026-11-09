import { describe, expect, it } from "vitest";
import { firstSentences, speakable } from "../src/voice/speakable.js";

describe("speakable", () => {
  it.each([
    ["Set `is_memory_enabled` to true.", "Set is memory enabled to true."],
    ["Calls ControllerLoop._iterate twice", "Calls Controller Loop iterate twice"],
    ["Edited `apps/desktop/src/voice/host.ts`.", "Edited host."],
    ["Updated host.ts and voice_agent.py.", "Updated host and voice agent."],
    ["1. Fix the timer\n2. Add VAD", "Fix the timer. Add VAD."],
    ["## Summary\n- Tests pass", "Summary. Tests pass."],
    ["| a | b |\n|---|---|\n| 1 | 2 |", ""],
    ["Here it is.\n```ts\nconst x = 1;\n```\nThat's all.", "Here it is. There's a code example on screen. That's all."],
    ["Start.\n```py\nprint(1)", "Start. There's a code example on screen."],
    ["See [the docs](https://x.y).", "See the docs."],
    ["Read https://example.com/a?b=1 first.", "Read first."],
    ["**Done.** Tests *pass*.", "Done. Tests pass."],
    ["Use v1.2 e.g. today.", "Use v1.2 e.g. today."],
  ])("%j is spoken as %j", (input, spoken) => {
    expect(speakable(input)).toBe(spoken);
  });
});

describe("firstSentences", () => {
  it("does not split after abbreviations, versions or list numbers", () => {
    expect(firstSentences("A e.g. B. C. D.", 2)).toBe("A e.g. B. C.");
    expect(firstSentences("Ship v1.2 now. Then rest.", 1)).toBe("Ship v1.2 now.");
  });
  it("returns the whole text when it has fewer sentences", () => {
    expect(firstSentences("Only one", 2)).toBe("Only one");
  });
});
