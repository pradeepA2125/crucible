export interface CaptureFrame {
  pcm: ArrayBuffer;
  rms: number;
}

export const CAPTURE_FRAME_BYTES = 640;
const PRE_ROLL_FRAMES = 13; // 260 ms
const ONSET_FRAMES = 4; // 80 ms
const MIN_VOICED_FRAMES = 8; // 160 ms across the utterance
const PAUSE_FRAMES = 28; // 560 ms: ask the voice agent whether the turn is finished
const WAIT_LIMIT_FRAMES = 400; // 8 seconds of quiet before asking whether to go ahead
const END_PADDING_FRAMES = 8; // 160 ms
const FRAME_MS = 20;
const PARTIAL_INTERVAL_FRAMES = 24; // 480 ms
const PLAYBACK_PARTIAL_FRAMES = 10; // Decode sooner after a possible barge-in.
const PLAYBACK_PAUSE_PROBE_FRAMES = 7; // Check short commands after 140 ms without finalizing them.
const MAX_FRAMES = 1_475; // 29.5 seconds per recognizer segment; longer speech rolls over
// Inside a turn, speech after a pause needs 80 ms at speech level: one stray frame of room
// noise (a click, traffic) used to count, cancelling every decision (measured 2026-11-10).
const RESUME_FRAMES = 4;
// The room's level is relearned during a turn from the quieter end of the last 3 seconds,
// capped so a loud talker's own speech never becomes "the floor".
const FLOOR_WINDOW_FRAMES = 150;
const FLOOR_MIN_FRAMES = 25;
const FLOOR_PERCENTILE = 0.2;
const FLOOR_CAP = 0.006;

interface SpeechCallbacks {
  start(onsetAt: number): void;
  partial(pcm: ArrayBuffer): void;
  pause(pcm: ArrayBuffer, cleanSpeechMs: number): void;
  resume(silenceMs: number): void;
  waitLimit(): void;
  rollover(pcm: ArrayBuffer): void;
  finish(pcm: ArrayBuffer): void;
  discard(): void;
}

function joinFrames(frames: readonly ArrayBuffer[], requested = frames.length): ArrayBuffer {
  const count = Math.max(0, Math.min(frames.length, requested));
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
  // Past a rollover the turn continues with an empty segment buffer.
  private rolled = false;
  private recent: number[] = [];
  private loudRun = 0;
  // Sound kept going without new words: it no longer even holds the reply.
  private holding = false;
  // Sound at speech level during a pause: the transcript decides whether it was words.
  private soundInPause = false;
  private quietBeforeSound: number | undefined;
  private soundThisFrame = false;

  constructor(private readonly callbacks: SpeechCallbacks) {}

  /** The thresholds speech detection is using right now, for diagnostics. */
  get levels() {
    const floor = this.floor();
    return {
      noise: floor,
      continueAt: Math.max(0.005, floor * 2),
      onsetAt: Math.max(0.008, floor * 3.5),
      paused: this.paused,
      held: this.holding,
      inTurn: this.inTurn,
    };
  }

  get held(): boolean {
    return this.holding;
  }

  /** The last frame counted as sound (it reset the quiet count). */
  get sounding(): boolean {
    return this.soundThisFrame;
  }

  private get inTurn(): boolean {
    return this.utterance.length > 0 || this.rolled;
  }

  /** The learned pre-speech level, or the quieter end of the turn's recent audio if higher. */
  private floor(): number {
    if (this.recent.length < FLOOR_MIN_FRAMES) return this.noise;
    const sorted = [...this.recent].sort((a, b) => a - b);
    return Math.max(this.noise, Math.min(FLOOR_CAP, sorted[Math.floor(sorted.length * FLOOR_PERCENTILE)]));
  }

  /** The recognizer heard no new words in sound that kept going: treat it as a pause, and
   * stop letting that sound hold the reply. Only new words (wordsResumed) resume the turn. */
  holdForWords() {
    if (!this.inTurn || this.holding) return;
    this.holding = true;
    if (this.paused) return;
    this.paused = true;
    this.silence = Math.max(this.silence, PAUSE_FRAMES);
    this.callbacks.pause(joinFrames(this.utterance), this.cleanVoiced * FRAME_MS);
  }

  /** The recognizer found new words: the speaker resumed. The only way a pause ends. */
  wordsResumed() {
    this.holding = false;
    if (!this.paused) return;
    const quietMs = this.quietBeforeSound ?? this.silence * FRAME_MS;
    this.paused = false;
    this.silence = 0;
    this.soundInPause = false;
    this.quietBeforeSound = undefined;
    this.callbacks.resume(quietMs);
  }

  /** Quiet since the last speech in the current turn; 0 outside a turn. */
  get silenceMs(): number {
    return this.utterance.length || this.rolled ? this.silence * FRAME_MS : 0;
  }

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
    this.rolled = false;
    this.recent = [];
    this.loudRun = 0;
    this.holding = false;
    this.soundInPause = false;
    this.quietBeforeSound = undefined;
  }

  finish() {
    if (!this.utterance.length && !this.rolled) {
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
    if (this.utterance.length || this.rolled) {
      this.continueSpeech(pcm, rms, playing, wantsPartial, manual, clean);
      return;
    }

    // Capture quiet speech even over playback. Only recognized words can stop the audio.
    const aboveNoise = rms >= Math.max(0.008, this.noise * 3.5);
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
    if (!playing) {
      this.recent.push(rms);
      if (this.recent.length > FLOOR_WINDOW_FRAMES) this.recent.shift();
    }
    const { continueAt, onsetAt } = this.levels;
    this.loudRun = rms >= onsetAt ? this.loudRun + 1 : 0;
    const sounding = rms >= continueAt;
    if (sounding) {
      this.voiced += 1;
      if (clean) this.cleanVoiced += 1;
    }
    let soundEnded = false;
    const quietBefore = this.silence;
    if (!this.paused) {
      this.silence = sounding ? 0 : this.silence + 1;
    } else if (this.holding) {
      this.silence += 1;
    } else if (this.loudRun >= RESUME_FRAMES) {
      // Maybe speaking again: hold any reply until the transcript says whether these were words.
      if (this.silence > 0) this.quietBeforeSound ??= this.silence * FRAME_MS;
      this.silence = 0;
      this.soundInPause = true;
    } else if (this.loudRun === 0) {
      this.silence += 1;
      soundEnded = this.soundInPause;
      this.soundInPause = false;
    } // else: a run that may yet prove to be speech holds the quiet count until it decides.
    this.soundThisFrame = this.silence === 0 && (quietBefore > 0 || sounding);
    if (this.utterance.length >= MAX_FRAMES) {
      const segment = joinFrames(this.utterance);
      this.utterance = [];
      this.rolled = true;
      this.nextPartial = PARTIAL_INTERVAL_FRAMES;
      this.callbacks.rollover(segment);
    } else if (!manual && !this.paused && this.silence === PAUSE_FRAMES) {
      if (this.voiced < MIN_VOICED_FRAMES) {
        this.finish();
        return;
      }
      this.paused = true;
      this.callbacks.pause(joinFrames(this.utterance, this.utterance.length - this.silence + END_PADDING_FRAMES),
        this.cleanVoiced * 20);
    } else if (!manual && this.silence === WAIT_LIMIT_FRAMES) {
      this.callbacks.waitLimit();
    } else if (wantsPartial && !manual && playing && this.voiced >= MIN_VOICED_FRAMES &&
      this.silence === PLAYBACK_PAUSE_PROBE_FRAMES) {
      this.callbacks.partial(joinFrames(this.utterance));
    } else if (soundEnded && wantsPartial && !manual) {
      // A short word in a pause may end before the next partial is due: check it now.
      this.nextPartial = this.utterance.length + PARTIAL_INTERVAL_FRAMES;
      this.callbacks.partial(joinFrames(this.utterance));
    } else if (wantsPartial && (this.silence === 0 || this.holding) && this.utterance.length >= this.nextPartial) {
      this.nextPartial = this.utterance.length + PARTIAL_INTERVAL_FRAMES;
      this.callbacks.partial(joinFrames(this.utterance));
    }
  }
}
