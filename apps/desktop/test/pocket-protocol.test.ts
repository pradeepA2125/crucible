import { describe, expect, it, vi } from "vitest";
import { servePocket, type PocketEngine } from "../src/voice/pocket-protocol.js";

type Event = { type: string; id?: string; pcm?: string; sampleRate?: number; error?: string };

/** An engine that yields `chunks` chunks of 0.25 s at 24 kHz, one per macrotask. */
function fakeEngine(chunks = 3, fail?: (text: string) => boolean): PocketEngine & { stopped: number } {
  const engine = {
    sampleRate: 24_000,
    stopped: 0,
    async generate(text: string, onChunk: (samples: Float32Array) => boolean) {
      if (fail?.(text)) throw new Error("unsupported characters");
      for (let index = 0; index < chunks; index += 1) {
        await new Promise(resolve => setTimeout(resolve, 0));
        if (!onChunk(new Float32Array(6_000).fill(0.5))) { engine.stopped += 1; return; }
      }
    },
  };
  return engine;
}

function harness(engine: PocketEngine) {
  const events: Event[] = [];
  const receive = servePocket(engine, line => events.push(JSON.parse(line) as Event));
  const speak = (id: string, text: string) => receive(JSON.stringify({ type: "speak", id, text }));
  const of = (id: string) => events.filter(event => event.id === id).map(event => event.type);
  return { events, receive, speak, of };
}

describe("Pocket worker protocol", () => {
  it("announces ready, streams audio, then done", async () => {
    const { events, speak, of } = harness(fakeEngine());
    expect(events[0]).toEqual({ type: "ready" });
    speak("a", "Hello there.");
    await vi.waitFor(() => expect(of("a").at(-1)).toBe("done"));
    const audio = events.filter(event => event.type === "audio");
    expect(audio.length).toBeGreaterThanOrEqual(1);
    expect(audio.every(event => event.sampleRate === 24_000)).toBe(true);
    const bytes = audio.reduce((total, event) => total + Buffer.from(event.pcm ?? "", "base64").length, 0);
    expect(bytes).toBe(3 * 6_000 * 2);
    expect(audio.every(event => Buffer.from(event.pcm ?? "", "base64").length <= 262_144)).toBe(true);
  });

  it("a new speak cancels the previous one", async () => {
    const engine = fakeEngine(50);
    const { speak, of } = harness(engine);
    speak("a", "A long sentence.");
    await vi.waitFor(() => expect(of("a")).toContain("audio"));
    speak("b", "Another.");
    await vi.waitFor(() => expect(of("b").at(-1)).toBe("done"));
    expect(of("a").at(-1)).toBe("done");
    expect(of("a").filter(type => type === "audio").length).toBeLessThan(50);
    expect(engine.stopped).toBe(1);
  });

  it("oversized or unspeakable text errors without stopping the worker", async () => {
    const { speak, of } = harness(fakeEngine(1, text => text.includes("🙂")));
    speak("big", "x".repeat(4_097));
    speak("empty", "   ");
    speak("emoji", "🙂🙂");
    await vi.waitFor(() => expect(of("emoji")).toEqual(["error"]));
    expect(of("big")).toEqual(["error"]);
    expect(of("empty")).toEqual(["error"]);
    speak("ok", "Fine.");
    await vi.waitFor(() => expect(of("ok").at(-1)).toBe("done"));
  });

  it("cancel stops a long generation", async () => {
    const engine = fakeEngine(50);
    const { receive, speak, of } = harness(engine);
    speak("a", "A long sentence.");
    await vi.waitFor(() => expect(of("a")).toContain("audio"));
    receive(JSON.stringify({ type: "cancel", id: "a" }));
    await vi.waitFor(() => expect(of("a").at(-1)).toBe("done"));
    expect(engine.stopped).toBe(1);
  });

  it("ignores malformed lines and invalid ids", () => {
    const { events, receive, speak } = harness(fakeEngine());
    receive("not json");
    speak("bad id with spaces", "Hello.");
    expect(events).toEqual([{ type: "ready" }]);
  });
});
