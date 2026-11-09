/* This entry runs in the AudioWorklet global, separate from the renderer. */
declare const sampleRate: number;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(name: string, processor: typeof AudioWorkletProcessor): void;

const TARGET_SAMPLE_RATE = 16_000;
const FRAME_SAMPLES = 320;

class CrucibleVoiceCapture extends AudioWorkletProcessor {
  private readonly samplesPerOutput = sampleRate / TARGET_SAMPLE_RATE;
  private weight = 0;
  private total = 0;
  private squareSum = 0;
  private offset = 0;
  private frame = new ArrayBuffer(FRAME_SAMPLES * 2);
  private pcm = new DataView(this.frame);

  process(inputs: Float32Array[][]): boolean {
    const channels = inputs[0];
    if (!channels?.length) return true;
    const length = channels[0].length;
    for (let index = 0; index < length; index += 1) {
      let sample = 0;
      for (const channel of channels) sample += channel[index];
      this.resample(sample / channels.length);
    }
    return true;
  }

  private resample(sample: number) {
    // Integrate the input sample's contribution across output sample boundaries.
    // This handles fractional ratios such as 44.1 kHz to 16 kHz across render blocks.
    let inputWeight = 1;
    while (inputWeight > 1e-7) {
      const take = Math.min(inputWeight, this.samplesPerOutput - this.weight);
      inputWeight -= take;
      this.weight += take;
      this.total += sample * take;
      if (this.weight >= this.samplesPerOutput - 1e-7) {
        this.emitSample(this.total / this.samplesPerOutput);
        this.weight = 0;
        this.total = 0;
      }
    }
  }

  private emitSample(sample: number) {
    const clipped = Math.max(-1, Math.min(1, sample));
    this.pcm.setInt16(this.offset * 2, clipped < 0 ? clipped * 32_768 : clipped * 32_767, true);
    this.squareSum += clipped * clipped;
    this.offset += 1;
    if (this.offset < FRAME_SAMPLES) return;

    this.port.postMessage({ pcm: this.frame, rms: Math.sqrt(this.squareSum / FRAME_SAMPLES) }, [this.frame]);
    this.frame = new ArrayBuffer(FRAME_SAMPLES * 2);
    this.pcm = new DataView(this.frame);
    this.squareSum = 0;
    this.offset = 0;
  }
}

registerProcessor("crucible-voice-capture", CrucibleVoiceCapture);
export {};
