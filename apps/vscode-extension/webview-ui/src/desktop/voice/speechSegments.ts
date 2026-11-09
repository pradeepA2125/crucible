export interface CaptureFrame {
  pcm: ArrayBuffer;
  rms: number;
}

export const CAPTURE_FRAME_BYTES = 640;
const PRE_ROLL_FRAMES = 13; // 260 ms
const ONSET_FRAMES = 4; // 80 ms
const MIN_VOICED_FRAMES = 8; // 160 ms across the utterance
const PAUSE_FRAMES = 28; // 560 ms
const RECHECK_FRAMES = 75; // 1.5 seconds, leaving time to verify a borderline transcript
const SETTLE_FRAMES = 125; // 2.5 seconds before forwarding an automatic utterance
const MAX_PAUSE_FRAMES = 225; // 4.5 seconds to allow a second decode on long utterances
const END_PADDING_FRAMES = 8; // 160 ms
const PARTIAL_INTERVAL_FRAMES = 24; // 480 ms
const PLAYBACK_PARTIAL_FRAMES = 10; // Decode sooner after a possible barge-in.
const PLAYBACK_PAUSE_PROBE_FRAMES = 7; // Check short commands after 140 ms without finalizing them.
const MAX_FRAMES = 1_475; // 29.5 seconds, including pre-roll

interface SpeechCallbacks {
  start(onsetAt: number): void;
  partial(pcm: ArrayBuffer): void;
  pause(pcm: ArrayBuffer, cleanSpeechMs: number): void;
  settle(): void;
  recheck(pcm: ArrayBuffer): void;
  resume(): void;
  pauseExpired(): void;
  finish(pcm: ArrayBuffer): void;
  discard(): void;
  tooLong(): void;
}

function joinFrames(frames: readonly ArrayBuffer[], count = frames.length): ArrayBuffer {
  const pcm = new ArrayBuffer(count * CAPTURE_FRAME_BYTES);
  const bytes = new Uint8Array(pcm);
  for (let index = 0; index < count; index += 1) {
    bytes.set(new Uint8Array(frames[index]), index * CAPTURE_FRAME_BYTES);
  }
  return pcm;
}

export class SpeechSegments {
  private preRoll: ArrayBuffer[] = [];
  private utterance: ArrayBuffer[] = [];
  private onset = 0;
  private onsetAt = 0;
  private silence = 0;
  private voiced = 0;
  private cleanVoiced = 0;
  private paused = false;
  private noise = 0.0025;
  private nextPartial = PRE_ROLL_FRAMES + PARTIAL_INTERVAL_FRAMES;
  private waitForQuiet = false;

  constructor(private readonly callbacks: SpeechCallbacks) {}

  reset() {
    this.resetUtterance();
    this.noise = 0.0025;
  }

  private resetUtterance() {
    this.preRoll = [];
    this.utterance = [];
    this.onset = 0;
    this.onsetAt = 0;
    this.silence = 0;
    this.voiced = 0;
    this.cleanVoiced = 0;
    this.paused = false;
    this.nextPartial = PRE_ROLL_FRAMES + PARTIAL_INTERVAL_FRAMES;
    this.waitForQuiet = false;
  }

  finish() {
    if (!this.utterance.length) {
      this.resetUtterance();
      return;
    }
    const audio = this.voiced >= MIN_VOICED_FRAMES
      ? joinFrames(this.utterance, this.utterance.length - Math.max(0, this.silence - END_PADDING_FRAMES))
      : null;
    this.resetUtterance();
    if (audio) this.callbacks.finish(audio);
    else this.callbacks.discard();
  }

  accept({ pcm, rms }: CaptureFrame, playing: boolean, wantsPartial: boolean, manual = false, clean = !playing) {
    if (!(pcm instanceof ArrayBuffer) || pcm.byteLength !== CAPTURE_FRAME_BYTES || !Number.isFinite(rms)) return;
    if (this.utterance.length) {
      this.continueSpeech(pcm, rms, playing, wantsPartial, manual, clean);
      return;
    }

    // Capture quiet speech even over playback. Only recognized words can stop the audio.
    const aboveNoise = rms >= Math.max(0.008, this.noise * 3.5);
    if (this.waitForQuiet) {
      this.silence = aboveNoise ? 0 : this.silence + 1;
      if (this.silence >= PAUSE_FRAMES) this.resetUtterance();
      return;
    }

    this.preRoll.push(pcm);
    if (this.preRoll.length > PRE_ROLL_FRAMES) this.preRoll.shift();
    if (aboveNoise && this.onset === 0) this.onsetAt = performance.now();
    this.onset = aboveNoise ? this.onset + 1 : 0;
    if (!aboveNoise && !playing) this.noise = Math.max(0.001, this.noise * 0.97 + rms * 0.03);
    if (this.onset >= ONSET_FRAMES) {
      this.utterance = this.preRoll;
      this.preRoll = [];
      this.voiced = this.onset;
      this.cleanVoiced = clean ? this.onset : 0;
      if (playing) this.nextPartial = this.utterance.length + PLAYBACK_PARTIAL_FRAMES;
      this.callbacks.start(this.onsetAt);
    }
  }

  private continueSpeech(pcm: ArrayBuffer, rms: number, playing: boolean, wantsPartial: boolean, manual: boolean, clean: boolean) {
    this.utterance.push(pcm);
    if (rms >= Math.max(0.005, this.noise * 2)) {
      this.voiced += 1;
      if (clean) this.cleanVoiced += 1;
      this.silence = 0;
      if (this.paused) {
        this.paused = false;
        this.callbacks.resume();
      }
    } else {
      this.silence += 1;
    }
    if (this.utterance.length >= MAX_FRAMES) {
      this.resetUtterance();
      this.waitForQuiet = true;
      this.callbacks.tooLong();
    } else if (!manual && this.silence === PAUSE_FRAMES) {
      if (this.voiced < MIN_VOICED_FRAMES) {
        this.finish();
        return;
      }
      this.paused = true;
      this.callbacks.pause(joinFrames(this.utterance, this.utterance.length - this.silence + END_PADDING_FRAMES),
        this.cleanVoiced * 20);
    } else if (!manual && this.silence === RECHECK_FRAMES) {
      this.callbacks.recheck(joinFrames(this.utterance));
    } else if (!manual && this.silence === SETTLE_FRAMES) {
      this.callbacks.settle();
    } else if (!manual && this.silence === MAX_PAUSE_FRAMES) {
      this.callbacks.pauseExpired();
    } else if (wantsPartial && !manual && playing && this.voiced >= MIN_VOICED_FRAMES &&
      this.silence === PLAYBACK_PAUSE_PROBE_FRAMES) {
      this.callbacks.partial(joinFrames(this.utterance));
    } else if (wantsPartial && this.silence === 0 && this.utterance.length >= this.nextPartial) {
      this.nextPartial = this.utterance.length + PARTIAL_INTERVAL_FRAMES;
      this.callbacks.partial(joinFrames(this.utterance));
    }
  }
}
