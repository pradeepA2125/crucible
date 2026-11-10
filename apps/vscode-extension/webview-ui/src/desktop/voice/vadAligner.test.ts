import { describe, expect, it } from "vitest";
import { VadAligner } from "./vadAligner";

const frame = (index: number) => ({ index });

describe("VadAligner: 32 ms probabilities back onto 20 ms frames", () => {
  it("releases a frame only once every window it overlaps has a probability", () => {
    const aligner = new VadAligner<{ index: number }>();
    for (let index = 0; index < 4; index += 1) aligner.push(frame(index)); // 1280 samples
    expect(aligner.take()).toEqual([]);
    aligner.addProbs([0.1, 0.9]); // windows 0-511, 512-1023
    // Frame 0 (0-319) is window 0; frame 1 (320-639) spans windows 0 and 1; frame 3 (960-1279) needs window 2.
    expect(aligner.take()).toEqual([
      { item: frame(0), prob: 0.1 },
      { item: frame(1), prob: 0.9 },
      { item: frame(2), prob: 0.9 },
    ]);
    aligner.addProbs([0.2]);
    expect(aligner.take()).toEqual([{ item: frame(3), prob: 0.9 }]); // the louder of windows 1 and 2
  });

  it("drain hands over waiting frames now and keeps later frames aligned", () => {
    const aligner = new VadAligner<{ index: number }>();
    for (let index = 0; index < 3; index += 1) aligner.push(frame(index));
    expect(aligner.drain()).toEqual([frame(0), frame(1), frame(2)]);
    aligner.push(frame(3)); // samples 960-1279: windows 1 and 2
    aligner.addProbs([0.1, 0.2, 0.7]);
    expect(aligner.take()).toEqual([{ item: frame(3), prob: 0.7 }]);
  });

  it("drops frames that are released only once", () => {
    const aligner = new VadAligner<{ index: number }>();
    aligner.push(frame(0));
    aligner.addProbs([0.5]);
    expect(aligner.take()).toHaveLength(1);
    expect(aligner.take()).toHaveLength(0);
  });

  it("keeps working after an hour of audio without keeping every probability", () => {
    const aligner = new VadAligner<{ index: number }>();
    for (let batch = 0; batch < 45_000; batch += 1) { // 45,000 × 80 ms = 1 hour
      for (let index = 0; index < 4; index += 1) aligner.push(frame(index));
      aligner.addProbs(batch % 8 === 0 ? [0.9, 0.9, 0.9] : [0.1, 0.1]);
      aligner.take();
    }
    expect((aligner as unknown as { probs: number[] }).probs.length).toBeLessThan(4);
  });

  it("counts samples across batches so windows stay aligned", () => {
    const aligner = new VadAligner<{ index: number }>();
    for (let index = 0; index < 8; index += 1) aligner.push(frame(index)); // 2560 samples = 5 windows
    aligner.addProbs([0, 0, 0, 0, 1]);
    expect(aligner.take().map(entry => entry.prob)).toEqual([0, 0, 0, 0, 0, 0, 1, 1]);
  });
});
