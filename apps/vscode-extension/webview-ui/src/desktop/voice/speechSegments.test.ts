import { describe, expect, it, vi } from "vitest";
import { CAPTURE_FRAME_BYTES, SpeechSegments } from "./speechSegments";

function fixture() {
  const callbacks = {
    start: vi.fn(), partial: vi.fn(), pause: vi.fn(), settle: vi.fn(), recheck: vi.fn(), resume: vi.fn(), pauseExpired: vi.fn(),
    finish: vi.fn(), discard: vi.fn(), tooLong: vi.fn(),
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
    expect(speech.settle).not.toHaveBeenCalled();
    speech.feed(12, 0.03);
    expect(speech.resume).toHaveBeenCalledOnce();
    speech.feed(224, 0);
    expect(speech.pause).toHaveBeenCalledTimes(2);
    expect(speech.settle).toHaveBeenCalledOnce();
    expect(speech.pauseExpired).not.toHaveBeenCalled();
    expect(speech.finish).not.toHaveBeenCalled();
    speech.feed(1, 0);
    expect(speech.start).toHaveBeenCalledOnce();
    expect(speech.pauseExpired).toHaveBeenCalledOnce();
  });

  it("extends a short unfinished phrase and allows an explicit send", () => {
    const speech = fixture();
    speech.feed(20, 0.03);
    speech.feed(30, 0);
    expect(speech.finish).not.toHaveBeenCalled();
    speech.segments.finish();
    expect(speech.finish).toHaveBeenCalledOnce();
  });

  it("discards an overlong capture and waits for silence before starting again", () => {
    const speech = fixture();
    speech.feed(1500, 0.03);
    expect(speech.tooLong).toHaveBeenCalledOnce();
    expect(speech.finish).not.toHaveBeenCalled();
    expect(speech.start).toHaveBeenCalledOnce();
    speech.feed(28, 0);
    speech.feed(4, 0.03);
    expect(speech.start).toHaveBeenCalledTimes(2);
  });
});
