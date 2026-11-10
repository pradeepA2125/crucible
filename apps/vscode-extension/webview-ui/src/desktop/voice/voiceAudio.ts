import type { CaptureFrame } from "./speechSegments";

const CAPTURE_WORKLET = "app://app/voice-capture.js";

interface CaptureOptions {
  onFrame(frame: CaptureFrame): void;
  onDeviceLost(): void;
}

export class VoiceAudio {
  /** Microphone capture. Playback has its own context so pausing a reply never stops capture. */
  private readonly context = new AudioContext();
  private readonly output = new AudioContext();
  private readonly sources = new Set<AudioBufferSourceNode>();
  /** Each scheduled buffer's loudness, in output time: what the microphone may hear back. */
  private levels: { start: number; end: number; rms: number }[] = [];
  private pausedOutput = false;
  private module: Promise<void> | undefined;
  private stream: MediaStream | undefined;
  private microphone: MediaStreamAudioSourceNode | undefined;
  private capture: AudioWorkletNode | undefined;
  private micGeneration = 0;
  private scheduledUntil = 0;
  private closed = false;

  constructor(private readonly onPlaying: (playing: boolean) => void) {}

  resume(): Promise<void> {
    return Promise.all([this.context.resume(), this.pausedOutput ? Promise.resolve() : this.output.resume()])
      .then(() => {});
  }

  get paused(): boolean {
    return this.pausedOutput;
  }

  /** Holds the reply where it is; audio still arriving is queued behind it. */
  pausePlayback() {
    if (this.closed || this.pausedOutput) return;
    this.pausedOutput = true;
    void this.output.suspend().catch(() => {});
  }

  resumePlayback() {
    if (this.closed || !this.pausedOutput) return;
    this.pausedOutput = false;
    void this.output.resume().catch(() => {});
  }

  /** RMS of the reply audio playing right now; 0 when silent or paused. */
  outputLevel(): number {
    if (this.pausedOutput || !this.playing) return 0;
    const now = this.output.currentTime;
    this.levels = this.levels.filter(level => level.end > now);
    return this.levels.find(level => level.start <= now)?.rms ?? 0;
  }

  get playing() {
    return this.sources.size > 0;
  }

  async openMicrophone({ onFrame, onDeviceLost }: CaptureOptions): Promise<boolean> {
    this.closeMicrophone();
    const generation = this.micGeneration;
    if (!navigator.mediaDevices?.getUserMedia || !this.context.audioWorklet) {
      throw new Error("This app could not access microphone capture.");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (this.closed || generation !== this.micGeneration) {
      for (const track of stream.getTracks()) track.stop();
      return false;
    }

    this.stream = stream;
    try {
      this.module ??= this.context.audioWorklet.addModule(CAPTURE_WORKLET).catch(error => {
        this.module = undefined;
        throw error;
      });
      await this.module;
      if (this.closed || generation !== this.micGeneration) return false;

      const microphone = this.context.createMediaStreamSource(stream);
      const capture = new AudioWorkletNode(this.context, "crucible-voice-capture", {
        numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      });
      this.microphone = microphone;
      this.capture = capture;
      capture.port.onmessage = event => {
        if (!this.closed && generation === this.micGeneration) onFrame(event.data as CaptureFrame);
      };
      capture.onprocessorerror = () => {
        if (generation === this.micGeneration) onDeviceLost();
      };
      for (const track of stream.getAudioTracks()) {
        track.addEventListener("ended", () => {
          if (generation === this.micGeneration) onDeviceLost();
        }, { once: true });
      }

      // The processor emits silence. Connecting it keeps Chromium's render graph running.
      microphone.connect(capture).connect(this.context.destination);
      return true;
    } catch (error) {
      if (generation === this.micGeneration) this.closeMicrophone();
      throw error;
    }
  }

  closeMicrophone() {
    this.micGeneration += 1;
    if (this.capture) {
      this.capture.port.onmessage = null;
      this.capture.port.close();
      this.capture.disconnect();
      this.capture = undefined;
    }
    this.microphone?.disconnect();
    this.microphone = undefined;
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = undefined;
  }

  setMicrophoneEnabled(enabled: boolean) {
    for (const track of this.stream?.getAudioTracks() ?? []) track.enabled = enabled;
  }

  play(pcm: ArrayBuffer, sampleRate: number) {
    if (this.closed || !(pcm instanceof ArrayBuffer) || pcm.byteLength < 2 || pcm.byteLength % 2
      || !Number.isInteger(sampleRate) || sampleRate < 8_000 || sampleRate > 96_000
      || pcm.byteLength > sampleRate * 20) return;
    const now = this.output.currentTime;
    if (this.scheduledUntil - now > 120) return;

    const bytes = new DataView(pcm);
    const frames = pcm.byteLength / 2;
    const buffer = this.output.createBuffer(1, frames, sampleRate);
    const samples = buffer.getChannelData(0);
    let energy = 0;
    for (let frame = 0; frame < frames; frame += 1) {
      samples[frame] = bytes.getInt16(frame * 2, true) / 32_768;
      energy += samples[frame] * samples[frame];
    }

    const source = this.output.createBufferSource();
    source.buffer = buffer;
    source.connect(this.output.destination);
    source.onended = () => {
      source.disconnect();
      if (this.sources.delete(source) && !this.playing) this.onPlaying(false);
    };
    const start = this.scheduledUntil > now ? this.scheduledUntil : now + 0.025;
    source.start(start);
    this.levels.push({ start, end: start + buffer.duration, rms: Math.sqrt(energy / frames) });
    const wasPlaying = this.playing;
    this.sources.add(source);
    this.scheduledUntil = start + buffer.duration;
    if (!wasPlaying) this.onPlaying(true);
  }

  /** A short, quiet two-note "hmm" that says a reply is coming. Returns its length in ms.
   * Not tracked as playback: it must not look like the assistant speaking. */
  cue(): number {
    if (this.closed) return 0;
    const start = this.output.currentTime + 0.01;
    const notes: readonly (readonly [number, number])[] = [[523.25, 0], [659.25, 0.16]];
    for (const [frequency, offset] of notes) {
      const tone = this.output.createOscillator();
      const gain = this.output.createGain();
      tone.type = "sine";
      tone.frequency.value = frequency;
      gain.gain.setValueAtTime(0, start + offset);
      gain.gain.linearRampToValueAtTime(0.05, start + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.18);
      tone.connect(gain).connect(this.output.destination);
      tone.onended = () => { tone.disconnect(); gain.disconnect(); };
      tone.start(start + offset);
      tone.stop(start + offset + 0.2);
    }
    return 380;
  }

  stopPlayback() {
    const wasPlaying = this.playing;
    const stoppedAt = performance.now();
    const stamp = wasPlaying ? this.output.getOutputTimestamp() : undefined;
    const estimatedStop = stamp?.performanceTime && stamp.contextTime !== undefined
      ? Math.max(stoppedAt, stamp.performanceTime + (this.output.currentTime - stamp.contextTime) * 1_000)
      : stoppedAt;
    for (const source of this.sources) {
      source.onended = null;
      source.stop();
      source.disconnect();
    }
    this.sources.clear();
    this.scheduledUntil = 0;
    this.levels = [];
    // A stopped reply must not leave the next one silent.
    if (this.pausedOutput) {
      this.pausedOutput = false;
      void this.output.resume().catch(() => {});
    }
    if (wasPlaying) this.onPlaying(false);
    return wasPlaying ? estimatedStop : undefined;
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.closeMicrophone();
    this.stopPlayback();
    void this.context.close().catch(() => {});
    void this.output.close().catch(() => {});
  }
}
