import { afterEach, expect, it, vi } from "vitest";

afterEach(() => vi.unstubAllGlobals());

it("resamples 44.1 kHz capture across render blocks into 16 kHz little-endian PCM", async () => {
  const postMessage = vi.fn();
  type Processor = { process(inputs: Float32Array[][]): boolean };
  let processor: Processor | undefined;
  vi.stubGlobal("sampleRate", 44_100);
  vi.stubGlobal("AudioWorkletProcessor", class { port = { postMessage }; });
  vi.stubGlobal("registerProcessor", (name: string, Worklet: new () => Processor) => {
    expect(name).toBe("crucible-voice-capture");
    processor = new Worklet();
  });
  await import("./capture-worklet");
  if (!processor) throw new Error("Voice capture processor was not registered");

  processor.process([[new Float32Array(511).fill(0.5)]]);
  expect(postMessage).not.toHaveBeenCalled();
  processor.process([[new Float32Array(371).fill(0.5)]]);
  expect(postMessage).toHaveBeenCalledOnce();
  const [frame, transfer] = postMessage.mock.calls[0] as [{ pcm: ArrayBuffer; rms: number }, ArrayBuffer[]];
  expect(frame.pcm.byteLength).toBe(640);
  expect(frame.rms).toBeCloseTo(0.5, 5);
  const samples = new DataView(frame.pcm);
  expect(samples.getInt16(0, true)).toBe(16_383);
  expect(samples.getInt16(638, true)).toBe(16_383);
  expect(transfer).toEqual([frame.pcm]);
});
