import { describe, expect, it, vi } from "vitest";
import { CAPTURE_FRAME_BYTES, SpeechSegments } from "./speechSegments";

function fixture() {
  const callbacks = {
    start: vi.fn(), partial: vi.fn(), pause: vi.fn(), resume: vi.fn(), waitLimit: vi.fn(),
    rollover: vi.fn(), finish: vi.fn(), discard: vi.fn(),
  };
  const segments = new SpeechSegments(callbacks);
  return {
    ...callbacks, segments,
    feed(count: number, rms: number, mark = 0, playing = false) {
      for (let i = 0; i < count; i += 1) {
        const pcm = new ArrayBuffer(CAPTURE_FRAME_BYTES);
        new DataView(pcm).setInt16(0, mark, true);
        segments.accept({ pcm, rms }, playing, true);
      }
    },
  };
}

describe("speech segmentation", () => {
  it("keeps the microphone lead-in, emits partial audio, and trims long endpoint silence", () => {
    const speech = fixture();
    speech.feed(15, 0, 1);
    speech.feed(3, 0.03, 2);
    expect(speech.start).not.toHaveBeenCalled();
    speech.feed(1, 0.03, 2);
    expect(speech.start).toHaveBeenCalledOnce();
    speech.feed(24, 0.03, 2);
    expect(speech.partial).toHaveBeenCalledOnce();
    speech.feed(27, 0, 3);
    expect(speech.finish).not.toHaveBeenCalled();
    speech.feed(1, 0, 3);
    expect(speech.finish).not.toHaveBeenCalled();
    expect(speech.pause).toHaveBeenCalledOnce();
    const [pcm] = speech.pause.mock.calls[0] as [ArrayBuffer];
    const samples = new DataView(pcm);
    expect(samples.byteLength).toBe((13 + 24 + 8) * CAPTURE_FRAME_BYTES);
    expect(samples.getInt16(0, true)).toBe(1);
    expect(samples.getInt16(9 * CAPTURE_FRAME_BYTES, true)).toBe(2);
    expect(samples.getInt16(37 * CAPTURE_FRAME_BYTES, true)).toBe(3);
  });

  it("ignores a brief spike and probes quiet overlap before the normal pause check", () => {
    const speech = fixture();
    speech.feed(3, 0.03, 0, true);
    speech.feed(1, 0, 0, true);
    expect(speech.start).not.toHaveBeenCalled();
    speech.feed(8, 0.012, 0, true);
    expect(speech.start).toHaveBeenCalledOnce();
    speech.feed(6, 0, 0, true);
    expect(speech.partial).not.toHaveBeenCalled();
    speech.feed(1, 0, 0, true);
    expect(speech.partial).toHaveBeenCalledOnce();
    expect(speech.pause).not.toHaveBeenCalled();
    speech.feed(21, 0, 0, true);
    expect(speech.pause).toHaveBeenCalledOnce();
    expect(speech.finish).not.toHaveBeenCalled();
  });

  it("keeps a long correction together across a thinking pause", () => {
    const speech = fixture();
    speech.feed(70, 0.03);
    speech.feed(100, 0);
    expect(speech.pause).toHaveBeenCalledOnce();
    expect(speech.segments.silenceMs).toBe(2000);
    speech.feed(12, 0.03);
    expect(speech.resume).not.toHaveBeenCalled(); // sound alone never resumes the turn
    expect(speech.segments.silenceMs).toBe(0); // but it holds any reply
    speech.segments.wordsResumed();
    expect(speech.resume).toHaveBeenCalledExactlyOnceWith(2000);
    speech.feed(399, 0);
    expect(speech.pause).toHaveBeenCalledTimes(2);
    expect(speech.waitLimit).not.toHaveBeenCalled();
    expect(speech.finish).not.toHaveBeenCalled();
    speech.feed(1, 0);
    expect(speech.waitLimit).toHaveBeenCalledOnce();
    speech.feed(100, 0);
    expect(speech.waitLimit).toHaveBeenCalledOnce();
    expect(speech.start).toHaveBeenCalledOnce();
  });

  it("does not reach the wait limit when speech resumes just before it", () => {
    const speech = fixture();
    speech.feed(20, 0.03);
    speech.feed(399, 0);
    speech.feed(5, 0.03);
    speech.feed(10, 0);
    expect(speech.waitLimit).not.toHaveBeenCalled();
  });

  it("extends a short unfinished phrase and allows an explicit send", () => {
    const speech = fixture();
    speech.feed(20, 0.03);
    speech.feed(30, 0);
    expect(speech.finish).not.toHaveBeenCalled();
    speech.segments.finish();
    expect(speech.finish).toHaveBeenCalledOnce();
  });

  it("rolls a long capture over into the next segment without starting a new turn", () => {
    const speech = fixture();
    speech.feed(1474, 0.03, 1);
    expect(speech.rollover).not.toHaveBeenCalled();
    speech.feed(1, 0.03, 1);
    expect(speech.rollover).toHaveBeenCalledOnce();
    const [pcm] = speech.rollover.mock.calls[0] as [ArrayBuffer];
    expect(pcm.byteLength).toBe(1475 * CAPTURE_FRAME_BYTES);
    speech.feed(40, 0.03, 2);
    speech.feed(28, 0);
    expect(speech.start).toHaveBeenCalledOnce();
    expect(speech.pause).toHaveBeenCalledOnce();
    const [segment] = speech.pause.mock.calls[0] as [ArrayBuffer];
    expect(segment.byteLength).toBe((40 + 8) * CAPTURE_FRAME_BYTES);
    expect(new DataView(segment).getInt16(0, true)).toBe(2);
  });

  it("never emits a negative segment when a rollover lands in a pause", () => {
    const speech = fixture();
    speech.feed(1470, 0.03);
    speech.feed(30, 0);
    expect(speech.rollover).toHaveBeenCalledOnce();
    expect(speech.pause).toHaveBeenCalledOnce();
    const [segment] = speech.pause.mock.calls[0] as [ArrayBuffer];
    expect(segment.byteLength).toBeGreaterThanOrEqual(0);
  });
});

describe("speech detection inside a turn", () => {
  const pause = (speech: ReturnType<typeof fixture>) => {
    speech.feed(20, 0.03);
    speech.feed(28, 0);
    expect(speech.pause).toHaveBeenCalledOnce();
  };

  it("one faint frame during a pause is not speech; a sustained one is", () => {
    const speech = fixture();
    pause(speech);
    speech.feed(1, 0.009);
    speech.feed(10, 0);
    speech.feed(1, 0.0098);
    speech.feed(10, 0);
    expect(speech.resume).not.toHaveBeenCalled();
    expect(speech.segments.silenceMs).toBe(48 * 20); // each faint frame holds the count, never resets it
    speech.feed(4, 0.03);
    expect(speech.resume).not.toHaveBeenCalled();
    expect(speech.segments.silenceMs).toBe(0);
    speech.segments.wordsResumed();
    expect(speech.resume).toHaveBeenCalledExactlyOnceWith(960);
  });

  it("sound in a pause asks for a transcript, holds the reply, and never re-pauses on its own", () => {
    const speech = fixture();
    pause(speech);
    speech.feed(10, 0.03); // a short word: shorter than the partial interval
    speech.feed(1, 0);
    expect(speech.partial).toHaveBeenCalled(); // asked as the sound ended
    speech.feed(60, 0);
    expect(speech.pause).toHaveBeenCalledOnce();
    expect(speech.resume).not.toHaveBeenCalled();
    expect(speech.segments.silenceMs).toBe(61 * 20);
  });

  it("steady room noise inside a turn stops counting as speech", () => {
    const speech = fixture();
    speech.feed(20, 0.03);
    for (let index = 0; index < 120; index += 1) speech.feed(1, index % 2 ? 0.007 : 0.004);
    expect(speech.pause).toHaveBeenCalledOnce();
  });

  it("held for words: sound neither resumes nor resets the quiet, and partials keep coming", () => {
    const speech = fixture();
    speech.feed(20, 0.03);
    speech.feed(60, 0.03);
    expect(speech.pause).not.toHaveBeenCalled();
    speech.segments.holdForWords();
    expect(speech.pause).toHaveBeenCalledOnce();
    expect(speech.segments.held).toBe(true);
    const partials = speech.partial.mock.calls.length;
    speech.feed(60, 0.03);
    expect(speech.resume).not.toHaveBeenCalled();
    expect(speech.segments.silenceMs).toBeGreaterThanOrEqual(60 * 20);
    expect(speech.partial.mock.calls.length).toBeGreaterThan(partials);
    speech.segments.wordsResumed();
    expect(speech.resume).toHaveBeenCalledOnce();
    expect(speech.segments.held).toBe(false);
  });

  it("held for words still reaches the wait limit", () => {
    const speech = fixture();
    speech.feed(20, 0.03);
    speech.segments.holdForWords();
    speech.feed(400, 0.03);
    expect(speech.waitLimit).toHaveBeenCalledOnce();
  });
});

describe("speech detection with a voice probability (Silero VAD)", () => {
  const feedVad = (speech: ReturnType<typeof fixture>, count: number, rms: number, prob: number) => {
    for (let index = 0; index < count; index += 1) {
      speech.segments.accept({ pcm: new ArrayBuffer(CAPTURE_FRAME_BYTES), rms }, false, true, false, true, prob);
    }
  };

  it("loud noise never starts a turn", () => {
    const speech = fixture();
    feedVad(speech, 100, 0.08, 0.03);
    expect(speech.start).not.toHaveBeenCalled();
  });

  it("quiet speech starts a turn after 80 ms", () => {
    const speech = fixture();
    feedVad(speech, 3, 0.004, 0.9);
    expect(speech.start).not.toHaveBeenCalled();
    feedVad(speech, 1, 0.004, 0.9);
    expect(speech.start).toHaveBeenCalledOnce();
  });

  it("loud noise inside a turn counts as quiet, so the pause comes on time", () => {
    const speech = fixture();
    feedVad(speech, 20, 0.03, 0.9);
    feedVad(speech, 28, 0.08, 0.05);
    expect(speech.pause).toHaveBeenCalledOnce();
  });

  it("0.35 keeps speech going but does not start it", () => {
    const speech = fixture();
    feedVad(speech, 20, 0.02, 0.4);
    expect(speech.start).not.toHaveBeenCalled();
    feedVad(speech, 4, 0.02, 0.9);
    feedVad(speech, 60, 0.02, 0.4);
    expect(speech.pause).not.toHaveBeenCalled();
  });

  it("noise in a pause neither resumes nor holds the reply", () => {
    const speech = fixture();
    feedVad(speech, 20, 0.03, 0.9);
    feedVad(speech, 28, 0, 0.02);
    feedVad(speech, 20, 0.1, 0.05); // a horn
    expect(speech.segments.silenceMs).toBe(48 * 20);
    expect(speech.resume).not.toHaveBeenCalled();
  });
});

describe("a rollover never hides the pause or the wait limit", () => {
  it("fires each once whatever frame the 30 s rollover lands on", () => {
    for (let speech = 1050; speech <= 1100; speech += 1) {
      const segments = fixture();
      segments.feed(speech, 0.03);
      segments.feed(500, 0);
      expect(segments.pause, `speech=${speech}`).toHaveBeenCalledOnce();
      expect(segments.waitLimit, `speech=${speech}`).toHaveBeenCalledOnce();
    }
  });
});
