import { SpeechSegments, VAD_START, type CaptureFrame } from "./speechSegments";
import {
  addsWords, clearOverlapRequest, hasSubstantiveSpeech, isBackchannel, isPlaybackControl, isPlaybackEcho, spokenMessage,
  spokenWordCount,
} from "./speechText";
import { DISPATCH_HOLD_MS, commitFloorMs } from "./turnTiming";
import { VadAligner } from "./vadAligner";
import { EchoGate } from "./echoGate";
import { VoiceAudio } from "./voiceAudio";
import {
  decisionOf, refusalMessageOf, refusalOf, voiceRequest,
  type VoiceBridge, type VoiceDecision, type VoiceEvent, type VoiceIntent, type VoiceReceipt, type VoiceRecognizer,
  type VoiceSynthesizer,
} from "./voiceBridge";

export interface VoiceSessionState {
  connected: boolean;
  microphone: "off" | "starting" | "on";
  capturing: boolean;
  checking: boolean;
  /** The voice agent heard an unfinished thought and is waiting for more. */
  waiting: boolean;
  /** The voice agent is working out whether the user finished and what to do. */
  deciding: boolean;
  /** That decision is taking long enough to tell the user (a cue plays once). */
  slowDecision: boolean;
  speaking: boolean;
  pending: number;
  partial: string;
  playbackStopMs?: number;
  reviews: readonly VoiceReview[];
}

export const INITIAL_VOICE_STATE: VoiceSessionState = {
  connected: false, microphone: "off", capturing: false, checking: false, waiting: false, deciding: false,
  slowDecision: false, speaking: false, pending: 0, partial: "", reviews: [],
};

interface VoiceReview {
  id: string;
  transcript: string;
  status?: "sending" | "failed";
}

interface VoiceSessionCallbacks {
  state(patch: Partial<VoiceSessionState>): void;
  /** decisionId: set for a decided spoken turn (its saved exchange carries it). */
  utterance(id: string, text: string, decisionId?: string): void;
  reply(id: string, text: string, final: boolean): void;
  receipt(receipt: VoiceReceipt): void;
  error(message: string): void;
  ended(reason?: string): void;
}

interface SpokenTurn {
  id: string;
  captureGeneration: number;
  snapshot: Promise<unknown>;
  playback: string | undefined;
  playbackTarget: string | undefined;
  onsetAt: number;
  /** Bumped whenever speech resumes: decisions for an older revision are stale. */
  revision: number;
  partialText?: string;
  /** Transcripts of segments that rolled over the recognizer's 30 s window, oldest first. */
  committed: string[];
  /** The whole request as heard at the latest pause. */
  text?: string;
  /** Speech over playback that is not yet clearly an instruction: never decided automatically. */
  held?: boolean;
  deciding?: number;
  /** The revision whose pause transcript is still being decoded. */
  decodingPause?: number;
  decodeFailed?: boolean;
  finalWanted?: boolean;
  decision?: VoiceDecision & { revision: number };
  /** Most words any transcript of this turn has had: growth is the evidence of speech. */
  maxWords: number;
  /** The turn as heard at its latest pause: a pause ends only on words beyond it. */
  pauseText?: string;
  /** Sound came during the pause and no transcript of it is back yet: nothing acts until it is. */
  awaitingWords?: boolean;
  /** This turn paused a reply: words stop it for good, no words resume it. */
  pausedReply?: boolean;
}

const playbackTailMs = 300;
// After the assistant asks a question, a one-word answer ("okay", "yes") is a real reply.
const answerWindowMs = 15_000;
const CLEAR_SPEECH_DECIDES_MS = 1_000;
const UNAVAILABLE = "The voice assistant could not decide what to do. Review the transcript and send it.";
// A decision still out after this long gets a quiet cue, so silence never reads as "not heard".
const SLOW_DECISION_MS = 1_200;
// The cue's own sound must not count as the user speaking again (one loud frame resumes speech).
const CUE_MASK_TAIL_MS = 200;

export type VoiceTraceValue = string | number | boolean | null;
/** Where diagnostics go (the desktop host's local log). Never affects the session. */
export type VoiceTrace = (sessionId: string, event: string, data: Record<string, VoiceTraceValue>) => void;

// Two seconds of sound with no new words is not speech (traffic, clicks, wind: measured
// 2026-11-10, where it held a turn open for three minutes): after that it stops holding the
// reply. Only new words (addsWords) ever end a pause.
const STALL_FRAMES = 100;
// With Silero, sound that reaches this point is a voice: only a TV or people nearby, so wait
// longer before deciding it brings no words (a slow transcript tripped 2 s mid-sentence live).
const STALL_FRAMES_WITH_VAD = 300;
// A paused reply resumes after this much quiet with no new words (a cough, "hmm", a door).
const RESUME_AFTER_QUIET_FRAMES = 50;
const LEVELS_IN_TURN_FRAMES = 50; // one line per second while a turn is open
const LEVELS_IDLE_FRAMES = 250; // one per five seconds otherwise
const round = (value: number) => Math.round(value * 10_000) / 10_000;

/** A microphone frame as captured, kept until its voice probability is known. */
interface CapturedFrame {
  frame: CaptureFrame;
  rms: number;
  /** The reply's output level when this frame was captured: the detector answers later. */
  outputRms: number;
  playing: boolean;
  clean: boolean;
  masked: boolean;
  generation: number;
}

// More than a second waiting for the detector means it cannot keep up: use loudness instead.
const VAD_BACKLOG_FRAMES = 50;

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function joined(parts: readonly string[]): string {
  return parts.map(part => part.trim()).filter(Boolean).join(" ");
}

function waitsForMore(decision: VoiceDecision): boolean {
  return decision.action === "start_task" || decision.action === "steer";
}

export class VoiceSession {
  private readonly audio: VoiceAudio;
  private readonly segments: SpeechSegments;
  private unsubscribe: (() => void) | undefined;
  private sessionId: string | undefined;
  private closed = false;
  private captureGeneration = 0;
  private spoken: SpokenTurn | undefined;
  private readonly reviews = new Map<string, { turn: SpokenTurn; draft: VoiceReview }>();
  private partialInFlight: Promise<void> | undefined;
  private decoding: Promise<void> = Promise.resolve();
  private latestInput: string | undefined;
  private outputTarget: string | undefined;
  private playbackText = "";
  private lastPlaybackAt = -Infinity;
  private pushToTalk = false;
  private holding = false;
  private queue: Promise<void> = Promise.resolve();
  private pending = 0;
  /** Pauses after which this speaker kept talking; they set how long to wait (turnTiming). */
  private readonly resumedPauses: number[] = [];
  private answerExpectedUntil = -Infinity;
  private cueMaskUntil = -Infinity;
  private playbackStartedAt = 0;
  private lastRms = 0;
  private lastPartialTraced = "";
  private meter = { frames: 0, peak: 0, sum: 0, loud: 0, probPeak: 0, probSum: 0, speech: 0, outputPeak: 0 };
  private framesSinceWords = 0;
  /** Silero VAD: on when the host started a detector, off for good after any failure. */
  private vad: "on" | "off" = "off";
  private readonly aligner = new VadAligner<CapturedFrame>();
  /** While a reply plays: the user's voice, not the reply's own sound coming back. */
  private readonly echoGate = new EchoGate();
  private vadBatch: ArrayBuffer[] = [];
  private vadInFlight = false;
  private vadAnswered = false;
  /** A partial asked for while another was decoding: the newer audio, decoded next. */
  private queuedPartial: ArrayBuffer | undefined;

  constructor(private readonly bridge: VoiceBridge, private readonly events: VoiceSessionCallbacks,
    private readonly traceSink?: VoiceTrace) {
    this.audio = new VoiceAudio(speaking => {
      const now = performance.now();
      if (speaking) this.playbackStartedAt = now;
      else this.lastPlaybackAt = now;
      this.trace("playback", { playing: speaking,
        ms: speaking ? 0 : Math.round(now - this.playbackStartedAt) });
      if (!this.closed) events.state({ speaking });
    });
    this.segments = new SpeechSegments({
      start: onsetAt => this.beginSpeech(onsetAt),
      partial: pcm => this.decodePartial(pcm),
      pause: (pcm, cleanSpeechMs) => this.decideAtPause(pcm, cleanSpeechMs),
      resume: silenceMs => this.resumeSpeech(silenceMs),
      waitLimit: () => this.reachWaitLimit(),
      rollover: pcm => this.rollOver(pcm),
      finish: pcm => this.finishSpeech(pcm),
      discard: () => this.clearCapture(false),
    });
  }

  /** voiceModel null is Automatic: the backend picks the provider's voice model. */
  async start(threadId: string, stt: VoiceRecognizer, tts: VoiceSynthesizer, voiceModel: string | null = null) {
    void this.audio.resume().catch(() => {});
    this.unsubscribe = this.bridge.subscribe(event => this.receive(event));
    const started = await voiceRequest(this.bridge, { type: "start", threadId, stt, tts,
      ...(voiceModel ? { voiceModel } : {}) });
    if (typeof started.sessionId !== "string" || !started.sessionId) {
      throw new Error("Crucible could not start a voice session.");
    }
    if (this.closed) {
      void voiceRequest(this.bridge, { type: "stop", sessionId: started.sessionId }).catch(() => {});
      return;
    }
    this.sessionId = started.sessionId;
    this.vad = started.vad === true ? "on" : "off";
    this.trace("vad", { running: this.vad === "on" });
    this.events.state({ connected: true });
    void this.unmute();
  }

  async unmute() {
    if (!this.sessionId || this.closed) return;
    const generation = ++this.captureGeneration;
    this.segments.reset();
    this.events.state({ microphone: "starting" });
    try {
      await this.audio.resume();
      if (!this.isCaptureCurrent(generation)) return;
      const opened = await this.audio.openMicrophone({
        onFrame: frame => {
          if (!this.isCaptureCurrent(generation) || (this.pushToTalk && !this.holding)) return;
          const playing = this.audio.playing;
          const masked = performance.now() < this.cueMaskUntil;
          const captured: CapturedFrame = {
            frame: masked ? { ...frame, rms: 0 } : frame, rms: frame.rms, outputRms: this.audio.outputLevel(),
            playing, masked, generation,
            clean: !playing && performance.now() - this.lastPlaybackAt > playbackTailMs,
          };
          // Push to talk needs no detector: the speaker decides when the turn ends.
          if (this.vad === "on" && !this.pushToTalk) {
            this.aligner.push(captured);
            this.vadBatch.push(frame.pcm);
            this.sendToDetector();
            return;
          }
          this.flushAligned();
          this.process(captured);
        },
        onDeviceLost: () => {
          if (!this.isCaptureCurrent(generation)) return;
          this.mute();
          this.events.error("The microphone disconnected. Turn it on to try again.");
        },
      });
      if (opened && this.isCaptureCurrent(generation)) {
        if (this.pushToTalk) this.audio.setMicrophoneEnabled(false);
        this.events.state({ microphone: "on" });
      }
    } catch (error) {
      if (!this.isCaptureCurrent(generation)) return;
      this.mute();
      this.events.error(error instanceof DOMException && error.name === "NotAllowedError"
        ? "Microphone access was denied. You can still type below."
        : "The microphone could not be opened. You can still type below.");
    }
  }

  mute() {
    const turn = this.spoken;
    if (turn && turn.playback !== undefined) this.keepForReview(turn, turn.text || turn.partialText || "");
    this.captureGeneration += 1;
    this.holding = false;
    this.clearCapture();
    this.audio.closeMicrophone();
    this.events.state({ microphone: "off" });
  }

  /** Takes effect on the next decision; null goes back to Automatic. */
  setVoiceModel(model: string | null) {
    if (!this.sessionId) return;
    this.trace("voice_model", { model });
    void voiceRequest(this.bridge, { type: "voiceModel", sessionId: this.sessionId, model })
      .catch(error => this.events.error(error instanceof Error ? error.message : String(error)));
  }

  setPushToTalk(enabled: boolean) {
    if (this.pushToTalk === enabled) return;
    this.cancelHold();
    this.clearCapture();
    this.pushToTalk = enabled;
    this.audio.setMicrophoneEnabled(!enabled);
  }

  beginHold() {
    if (!this.pushToTalk || this.holding || !this.sessionId || this.closed) return;
    this.holding = true;
    this.audio.setMicrophoneEnabled(true);
    if (this.audio.playing) this.stopSpeaking();
  }

  sendNow() {
    if (this.pushToTalk && !this.holding) return;
    if (this.holding) {
      this.holding = false;
      this.audio.setMicrophoneEnabled(false);
    }
    // The last ~100 ms may still wait for the detector: include it, and keep the stream aligned.
    this.sendToDetector(true);
    this.flushAligned();
    this.segments.finish();
  }

  cancelHold() {
    if (!this.holding) return;
    this.holding = false;
    this.audio.setMicrophoneEnabled(false);
    this.clearCapture();
  }

  stopSpeaking() {
    if (!this.sessionId || this.closed) return;
    void this.interrupt(this.sessionId, "stop_button", performance.now()).catch(error => {
      if (!this.closed) this.events.error(messageOf(error, "Could not stop the spoken reply."));
    });
  }

  private isCaptureCurrent(generation: number) {
    return !this.closed && this.captureGeneration === generation;
  }

  private interrupt(sessionId: string, reason: string, startedAt = performance.now()) {
    this.outputTarget = undefined;
    const wasPlaying = this.audio.playing;
    const stoppedAt = this.audio.stopPlayback();
    this.trace("interrupt", { reason, wasPlaying: wasPlaying || stoppedAt !== undefined });
    if (stoppedAt !== undefined) {
      this.events.state({ playbackStopMs: Math.round(Math.max(0, stoppedAt - startedAt)) });
    }
    return voiceRequest(this.bridge, { type: "interrupt", sessionId });
  }

  private snapshot(sessionId: string, utteranceId: string) {
    const snapshot = voiceRequest(this.bridge, { type: "snapshot", sessionId, utteranceId });
    void snapshot.catch(() => {}); // Finalization reports errors from this request.
    return snapshot;
  }

  private trace(event: string, data: Record<string, VoiceTraceValue> = {}) {
    const sessionId = this.sessionId;
    if (!sessionId || !this.traceSink || this.closed) return;
    try {
      this.traceSink(sessionId, event, data);
    } catch {
      // Diagnostics never affect the voice session.
    }
  }

  /** One frame through speech detection, with Silero's probability when the detector runs. */
  private process(captured: CapturedFrame, speechProb?: number) {
    if (!this.isCaptureCurrent(captured.generation) || this.closed) return;
    // Over a reply, only the user's voice counts: the reply's own echo is masked out.
    if (speechProb !== undefined && captured.playing
      && !this.echoGate.observe(captured.rms, captured.outputRms, speechProb)) speechProb = 0;
    this.lastRms = captured.rms;
    this.meter.outputPeak = Math.max(this.meter.outputPeak, captured.outputRms);
    this.measure(captured.rms, captured.masked, captured.playing, speechProb);
    this.segments.accept(captured.frame, captured.playing,
      !this.partialInFlight && (this.pending === 0 || captured.playing), this.pushToTalk, captured.clean, speechProb);
    this.commitWhenQuietEnough();
    this.watchForWords();
    this.resumeAfterQuiet();
  }

  /** The sound that paused a reply brought no words: carry on from where it paused. */
  private resumeAfterQuiet() {
    const turn = this.spoken;
    if (!turn?.pausedReply || this.segments.silenceMs < RESUME_AFTER_QUIET_FRAMES * 20) return;
    if (turn.awaitingWords || turn.decodingPause === turn.revision) return;
    this.clearCapture(); // resumes the reply
  }

  private resumeReply(turn: SpokenTurn) {
    if (!turn.pausedReply) return;
    turn.pausedReply = false;
    this.audio.resumePlayback();
    this.trace("resumed", { utteranceId: turn.id });
  }

  /** Sends captured audio to the detector, one request at a time, batching what waits. */
  private sendToDetector(flush = false) {
    const sessionId = this.sessionId;
    if (this.vad !== "on" || !sessionId || this.vadInFlight || !this.vadBatch.length) return;
    if (!flush && this.vadBatch.length < 4) return;
    if (this.vadBatch.length > VAD_BACKLOG_FRAMES) {
      this.vadOff("the speech detector fell behind");
      return;
    }
    const batch = this.vadBatch;
    this.vadBatch = [];
    const pcm = new Uint8Array(batch.reduce((total, part) => total + part.byteLength, 0));
    let offset = 0;
    for (const part of batch) { pcm.set(new Uint8Array(part), offset); offset += part.byteLength; }
    this.vadInFlight = true;
    void voiceRequest(this.bridge, { type: "vad", sessionId, pcm: pcm.buffer }).then(result => {
      this.vadInFlight = false;
      const probs = result.probs;
      if (!Array.isArray(probs) || !probs.every(value => typeof value === "number" && value >= 0 && value <= 1)) {
        this.vadOff("the speech detector stopped answering");
        return;
      }
      if (!this.vadAnswered) {
        this.vadAnswered = true;
        this.trace("vad_on", {});
      }
      this.aligner.addProbs(probs);
      for (const { item, prob } of this.aligner.take()) this.process(item, prob);
      this.sendToDetector();
    }).catch(error => {
      this.vadInFlight = false;
      this.vadOff(messageOf(error, "the speech detector failed"));
    });
  }

  /** Frames still waiting for a probability go through on loudness now. */
  private flushAligned() {
    for (const captured of this.aligner.drain()) this.process(captured);
  }

  private vadOff(reason: string) {
    if (this.vad === "off") return;
    this.vad = "off";
    this.vadBatch = [];
    this.trace("vad_off", { reason });
    this.flushAligned();
  }

  /** Microphone levels against the live thresholds: the evidence for "why did it (not) pause". */
  private measure(rms: number, masked: boolean, playing: boolean, speechProb?: number) {
    if (!Number.isFinite(rms)) return;
    const meter = this.meter;
    const levels = this.segments.levels;
    meter.frames += 1;
    meter.peak = Math.max(meter.peak, rms);
    meter.sum += rms;
    if (rms >= levels.continueAt) meter.loud += 1;
    if (speechProb !== undefined) {
      meter.probPeak = Math.max(meter.probPeak, speechProb);
      meter.probSum += speechProb;
      if (speechProb >= VAD_START) meter.speech += 1;
    }
    if (meter.frames < (levels.inTurn ? LEVELS_IN_TURN_FRAMES : LEVELS_IDLE_FRAMES)) return;
    this.trace("levels", {
      peak: round(meter.peak), mean: round(meter.sum / meter.frames), loudFrames: meter.loud, frames: meter.frames,
      noise: round(levels.noise), continueAt: round(levels.continueAt), onsetAt: round(levels.onsetAt),
      silenceMs: this.segments.silenceMs, inTurn: levels.inTurn, paused: levels.paused, playing, masked,
      vad: speechProb !== undefined, speechPeak: round(meter.probPeak), speechMean: round(meter.probSum / meter.frames),
      speechFrames: meter.speech, outputPeak: round(meter.outputPeak), coupling: round(this.echoGate.coupling()),
    });
    this.meter = { frames: 0, peak: 0, sum: 0, loud: 0, probPeak: 0, probSum: 0, speech: 0, outputPeak: 0 };
  }

  private beginSpeech(onsetAt: number) {
    const sessionId = this.sessionId;
    if (!sessionId || this.closed) return;
    const id = crypto.randomUUID();
    const playback = this.audio.playing || onsetAt - this.lastPlaybackAt <= playbackTailMs ? this.playbackText : undefined;
    const playbackTarget = playback === undefined ? undefined : this.outputTarget;
    this.latestInput = id;
    this.trace("onset", { utteranceId: id, overPlayback: playback !== undefined, rms: round(this.lastRms) });
    // With the detector, an onset over a reply is the user's voice (the echo gate saw to that):
    // hold the reply where it is until we know whether they said anything.
    const pausedReply = this.vad === "on" && !this.pushToTalk && this.audio.playing && !this.audio.paused;
    if (pausedReply) {
      this.audio.pausePlayback();
      this.trace("paused_for_user", { utteranceId: id });
    }
    this.spoken = {
      id, captureGeneration: this.captureGeneration, snapshot: this.snapshot(sessionId, id),
      playback, playbackTarget, onsetAt, revision: 0, committed: [], maxWords: 0, pausedReply,
    };
    this.framesSinceWords = 0;
    this.events.state({ capturing: true, partial: "" });
  }

  private clearCapture(reset = true) {
    // A turn that paused a reply and ends without words that stopped it (a cough too short to
    // be an utterance, a filler, its own echo): the reply carries on from where it paused.
    if (this.spoken?.pausedReply) this.resumeReply(this.spoken);
    this.spoken = undefined;
    if (reset) this.segments.reset();
    this.events.state({
      capturing: false, checking: false, waiting: false, deciding: false, slowDecision: false, partial: "",
    });
  }

  private decode(sessionId: string, turn: SpokenTurn, pcm: ArrayBuffer, final: boolean, valid: () => boolean) {
    const result = this.decoding.then(() => valid() && pcm.byteLength > 0 ? voiceRequest(this.bridge, {
      type: "decode", sessionId, utteranceId: turn.id, pcm, final,
    }) : undefined);
    this.decoding = result.then(() => {}, () => {});
    return result;
  }

  private decodePartial(pcm: ArrayBuffer) {
    const turn = this.spoken;
    const sessionId = this.sessionId;
    if (!turn || !sessionId || this.closed) return;
    if (this.partialInFlight) {
      // Never drop it: during a pause this audio is the only word check for the sound.
      this.queuedPartial = pcm;
      return;
    }
    const revision = turn.revision;
    const current = () => this.spoken === turn && turn.revision === revision && this.isCaptureCurrent(turn.captureGeneration);
    // Only audio captured during a pause can show the speaker resumed it.
    const duringPause = this.segments.levels.paused;
    const partial = this.decode(sessionId, turn, pcm, false, current).then(result => {
      if (current() && typeof result?.text === "string") {
        const partial = joined([...turn.committed, spokenMessage(result.text, turn.playback)]);
        // One word over a paused reply waits for the pause: it may be a cough Whisper heard
        // as "you", which must resume the reply, never end it.
        if (this.isCapturedPlaybackControl(turn, result.text) || hasSubstantiveSpeech(partial)) {
          this.stopForWords(turn, partial);
        }
        if (turn.playback === undefined || spokenWordCount(partial)) turn.partialText = partial;
        this.noteWords(turn, partial, duringPause);
        if (duringPause && !this.queuedPartial) turn.awaitingWords = false;
        if (partial !== this.lastPartialTraced) {
          this.lastPartialTraced = partial;
          this.trace("transcript", { utteranceId: turn.id, final: false, text: partial.slice(0, 1000) });
        }
        if (turn.deciding === undefined && !turn.decision) this.events.state({ partial });
      }
    }).catch(() => {
      // A partial is expendable. A failed final decode is shown to the user.
    }).finally(() => {
      if (this.partialInFlight !== partial) return;
      this.partialInFlight = undefined;
      const queued = this.queuedPartial;
      this.queuedPartial = undefined;
      if (queued && this.spoken === turn) this.decodePartial(queued);
      // The check could not run (a failed decode): let quiet decide rather than wait forever.
      else if (!queued && duringPause && !current()) turn.awaitingWords = false;
    });
    this.partialInFlight = partial;
  }

  /** A pause: transcribe the segment and ask the voice agent whether the turn is finished. */
  private decideAtPause(pcm: ArrayBuffer, cleanSpeechMs: number) {
    const turn = this.spoken;
    const sessionId = this.sessionId;
    if (!turn || !sessionId) return;
    const revision = turn.revision;
    const current = () => this.spoken === turn && turn.revision === revision && this.isCaptureCurrent(turn.captureGeneration);
    this.trace("pause", { utteranceId: turn.id, silenceMs: this.segments.silenceMs, cleanSpeechMs, revision });
    turn.pauseText = turn.partialText ?? turn.text ?? "";
    turn.decodingPause = revision;
    this.events.state({ checking: true });
    void this.decode(sessionId, turn, pcm, true, current).then(result => {
      const heard = typeof result?.text === "string" ? result.text : "";
      this.trace("transcript", { utteranceId: turn.id, final: true, text: heard.slice(0, 1000), stale: !current(),
        revision });
      if (!current()) return;
      turn.decodingPause = undefined;
      // The full transcript of the paused audio is the bar new words have to clear.
      const pauseText = joined([...turn.committed, heard]);
      turn.pauseText = pauseText;
      turn.maxWords = Math.max(turn.maxWords, spokenWordCount(pauseText));
      if (this.consumePlaybackControl(turn, heard)) {
        this.clearCapture();
        return;
      }
      const answering = performance.now() < this.answerExpectedUntil;
      const text = joined([...turn.committed, spokenMessage(heard, turn.playback)]);
      if (!text || (!answering && isBackchannel(text)) || (turn.playback !== undefined && !spokenWordCount(text))) {
        if (!turn.pausedReply && !heard.trim() && turn.playback !== undefined) this.keepForReview(turn, turn.partialText || "");
        this.clearCapture();
        return;
      }
      // A lone word or filler over a paused reply (a cough Whisper heard as "you", a "hmm")
      // is not a request: the reply resumes. Playback words ("stop", "wait") were handled
      // above; an answer to the reply's question still stops it.
      if (turn.pausedReply && !answering && spokenWordCount(text) < 2) {
        this.clearCapture();
        return;
      }
      if (hasSubstantiveSpeech(text) || turn.pausedReply) this.stopForWords(turn, text);
      turn.text = text;
      this.events.state({ partial: text });
      // A short reply to playback can be misheard as an instruction; it waits for review.
      // Once the reply has stopped and they have said a full second clearly, it is an ordinary
      // turn: the voice agent judges it (live, a request after "Awesome." over a check-in sat
      // held for 7 s of clear speech and only reached review).
      // With the detector, the echo gate already established it was the user: decide it.
      turn.held = this.vad !== "on" && turn.playback !== undefined && cleanSpeechMs < CLEAR_SPEECH_DECIDES_MS
        && (cleanSpeechMs < 420 || !clearOverlapRequest(text));
      if (turn.held) {
        this.events.state({ checking: false });
        if (turn.finalWanted) this.abandonAtLimit(turn);
        return;
      }
      // A lone word or filler ("hmm", "yes", Whisper's "you") is not a request unless it answers a question.
      if (!answering && spokenWordCount(text) < 2) {
        this.clearCapture();
        return;
      }
      this.requestDecision(turn, turn.finalWanted === true);
    }).catch(() => {
      if (!current()) return;
      turn.decodingPause = undefined;
      turn.decodeFailed = true;
      this.events.state({ checking: false });
      if (turn.finalWanted) this.abandonAtLimit(turn);
    });
  }

  private requestDecision(turn: SpokenTurn, finalCall: boolean) {
    const sessionId = this.sessionId;
    const text = turn.text;
    if (!sessionId || !text) return;
    const revision = turn.revision;
    const current = () => this.spoken === turn && turn.revision === revision && this.isCaptureCurrent(turn.captureGeneration);
    turn.deciding = revision;
    turn.finalWanted = false;
    const asked = performance.now();
    this.trace("decide", { utteranceId: turn.id, text: text.slice(0, 1000), finalCall,
      silenceMs: this.segments.silenceMs, revision });
    this.events.state({ checking: false, deciding: true });
    this.watchSlowDecision(turn, revision);
    const settled = () => {
      turn.deciding = undefined;
      this.events.state({ deciding: false, slowDecision: false });
    };
    void voiceRequest(this.bridge, {
      type: "decide", sessionId, utteranceId: turn.id, text, silenceMs: this.segments.silenceMs, finalCall,
    }).then(result => {
      const decision = decisionOf(result);
      this.trace("decision", {
        utteranceId: turn.id, ms: Math.round(performance.now() - asked), stale: !current(), revision, finalCall,
        action: decision?.action ?? null, turn: decision?.turn ?? null, say: decision?.say ?? null,
        refused: refusalOf(result) ?? null, reason: refusalMessageOf(result) ?? null,
      });
      if (!current()) return;
      settled();
      if (refusalOf(result) || !decision) {
        this.decisionFailed(turn, text, finalCall, refusalMessageOf(result));
        return;
      }
      if (decision.action === "listen") {
        this.events.state({ waiting: true });
        if (turn.finalWanted) this.requestDecision(turn, true);
        return;
      }
      this.events.state({ waiting: false });
      turn.decision = { ...decision, revision };
      if (finalCall) this.commitDecision(turn);
      else this.commitWhenQuietEnough();
    }).catch(error => {
      this.trace("decision", { utteranceId: turn.id, ms: Math.round(performance.now() - asked), stale: !current(),
        revision, finalCall, failed: messageOf(error, "unknown") });
      if (!current()) return;
      settled();
      this.decisionFailed(turn, text, finalCall, messageOf(error, ""));
    });
  }

  /** Counts frames of sound since the transcript last grew; past two seconds, that sound is
   * not speech, and it stops holding the turn open. */
  private watchForWords() {
    const turn = this.spoken;
    // A turn that began over a reply is watched like any other once that reply has stopped,
    // and one with no words at all is held too: either way noise can no longer keep it open.
    if (!turn || this.pushToTalk || (turn.playback !== undefined && this.audio.playing)) return;
    if (this.segments.held || !this.segments.sounding) return;
    if (this.segments.levels.paused) turn.awaitingWords = true;
    this.framesSinceWords += 1;
    const limit = this.vad === "on" ? STALL_FRAMES_WITH_VAD : STALL_FRAMES;
    if (this.framesSinceWords < limit) return;
    this.trace("hold_for_words", { utteranceId: turn.id, words: turn.maxWords,
      soundMs: this.framesSinceWords * 20, paused: this.segments.levels.paused });
    turn.awaitingWords = false; // that sound was not speech: it no longer holds anything back
    this.segments.holdForWords();
  }

  private noteWords(turn: SpokenTurn, partial: string, duringPause: boolean) {
    if (this.segments.levels.paused) {
      if (!duringPause) {
        // Audio from before the pause, transcribed late: part of what was paused on.
        if (spokenWordCount(partial) > spokenWordCount(turn.pauseText ?? "")) turn.pauseText = partial;
        return;
      }
      if (!addsWords(turn.pauseText ?? "", partial)) return;
      this.trace("words_resumed", { utteranceId: turn.id, before: (turn.pauseText ?? "").slice(-200),
        after: partial.slice(-200) });
      turn.maxWords = Math.max(turn.maxWords, spokenWordCount(partial));
      this.framesSinceWords = 0;
      this.segments.wordsResumed();
      return;
    }
    const count = spokenWordCount(partial);
    if (count > turn.maxWords) {
      turn.maxWords = count;
      this.framesSinceWords = 0;
    }
  }

  private watchSlowDecision(turn: SpokenTurn, revision: number) {
    setTimeout(() => {
      if (this.closed || this.spoken !== turn || turn.deciding !== revision) return;
      this.events.state({ slowDecision: true });
      this.trace("cue", { utteranceId: turn.id, playing: this.audio.playing });
      // Never over the assistant's own speech: that already says something is happening.
      if (this.audio.playing) return;
      this.cueMaskUntil = performance.now() + this.audio.cue() + CUE_MASK_TAIL_MS;
    }, SLOW_DECISION_MS);
  }

  /** A slow or failed decision is not a reason to drop the user's words: keep listening, and
   * let the wait limit ask once more. Only a failed final question falls back to review. */
  private decisionFailed(turn: SpokenTurn, text: string, finalCall: boolean, reason: string | undefined) {
    this.trace("decision_failed", { utteranceId: turn.id, finalCall, reason: reason ?? null });
    if (!finalCall) {
      this.events.state({ waiting: true });
      if (turn.finalWanted) this.requestDecision(turn, true);
      return;
    }
    this.clearCapture();
    this.keepForReview(turn, text);
    this.events.error(reason ? `The voice assistant is unavailable: ${reason} Review the transcript and send it.`
      : UNAVAILABLE);
  }

  /** Called on every microphone frame: acts once the speaker has been quiet long enough. */
  private commitWhenQuietEnough() {
    const turn = this.spoken;
    const decision = turn?.decision;
    if (!turn || !decision || decision.revision !== turn.revision) return;
    // Sound came in the pause: wait for the transcript to say whether the speaker went on.
    if (turn.awaitingWords) return;
    const required = commitFloorMs(this.resumedPauses) + (waitsForMore(decision) ? DISPATCH_HOLD_MS : 0);
    if (this.segments.silenceMs >= required) {
      this.trace("act", { utteranceId: turn.id, action: decision.action, requiredMs: required,
        silenceMs: this.segments.silenceMs });
      this.commitDecision(turn);
    }
  }

  private resumeSpeech(silenceMs: number) {
    if (silenceMs >= 400) {
      this.resumedPauses.push(silenceMs);
      if (this.resumedPauses.length > 50) this.resumedPauses.shift();
    }
    const turn = this.spoken;
    this.trace("resume", {
      utteranceId: turn?.id ?? null, quietMs: silenceMs,
      droppedDecision: !!turn && (turn.decision !== undefined || turn.deciding !== undefined),
    });
    if (turn) {
      turn.revision += 1;
      turn.decision = undefined;
      turn.deciding = undefined;
      turn.finalWanted = false;
      turn.partialText = undefined;
      // The next pause transcribes the whole segment again; the old pause's text is a stale prefix.
      turn.text = undefined;
      turn.held = false;
      turn.decodeFailed = false;
    }
    this.events.state({ checking: false, waiting: false, deciding: false, slowDecision: false });
  }

  private reachWaitLimit() {
    const turn = this.spoken;
    this.trace("wait_limit", { utteranceId: turn?.id ?? null, hasText: !!turn?.text,
      deciding: turn?.deciding !== undefined });
    if (!turn || turn.decision) return;
    // Still transcribing or deciding: that result asks the final question when it lands.
    if (turn.decodingPause === turn.revision || turn.deciding !== undefined) {
      turn.finalWanted = true;
      return;
    }
    if (turn.held || !turn.text) {
      this.abandonAtLimit(turn);
      return;
    }
    this.requestDecision(turn, true);
  }

  /** Nothing can be decided automatically: keep what was heard so it is never lost silently. */
  private abandonAtLimit(turn: SpokenTurn) {
    const text = turn.text || turn.partialText || "";
    this.clearCapture();
    if (this.latestInput === turn.id) this.keepForReview(turn, text);
    if (turn.decodeFailed) this.events.error("That spoken message could not be transcribed. Review it or type it below.");
  }

  private rollOver(pcm: ArrayBuffer) {
    const turn = this.spoken;
    const sessionId = this.sessionId;
    this.trace("rollover", { utteranceId: turn?.id ?? null });
    if (!turn || !sessionId) return;
    // Survives resumed speech and an explicit send that clears the turn before this lands:
    // this audio is final whatever happens next, and a later finish reads `committed`.
    const current = () => this.isCaptureCurrent(turn.captureGeneration);
    void this.decode(sessionId, turn, pcm, true, current).then(result => {
      if (!current() || typeof result?.text !== "string") return;
      const text = spokenMessage(result.text, turn.playback);
      if (text) turn.committed.push(text);
    }).catch(() => {
      if (current()) this.events.error("Part of that message could not be transcribed. Keep talking or type it below.");
    });
  }

  private commitDecision(turn: SpokenTurn) {
    const sessionId = this.sessionId;
    const decision = turn.decision;
    const text = turn.text ?? "";
    if (!sessionId || !decision) return;
    turn.decision = undefined;
    this.clearCapture();
    void this.enqueue(async () => {
      if (!this.isCaptureCurrent(turn.captureGeneration)) return;
      await turn.snapshot;
      await this.commitWith(sessionId, turn, text, decision, false);
    }).catch(error => this.commitFailed(turn, text, error));
  }

  /** Explicit send (push to talk): decide with no more waiting, then act. */
  private finalize(turn: SpokenTurn, text: string) {
    const sessionId = this.sessionId;
    if (!sessionId) return;
    void this.enqueue(async () => {
      if (!this.isCaptureCurrent(turn.captureGeneration)) return;
      await turn.snapshot;
      const decided = await this.decideFinal(sessionId, turn, text);
      if (!decided) {
        this.keepForReview(turn, text);
        this.events.error(UNAVAILABLE);
        return;
      }
      await this.commitWith(sessionId, turn, text, decided, false);
    }).catch(error => this.commitFailed(turn, text, error));
  }

  private async decideFinal(sessionId: string, turn: SpokenTurn, text: string): Promise<VoiceDecision | undefined> {
    const result = await voiceRequest(this.bridge, {
      type: "decide", sessionId, utteranceId: turn.id, text, silenceMs: this.segments.silenceMs, finalCall: true,
    });
    const decision = decisionOf(result);
    return refusalOf(result) || !decision || decision.action === "listen" ? undefined : decision;
  }

  private async commitWith(sessionId: string, turn: SpokenTurn, text: string, decision: VoiceDecision, retried: boolean) {
    this.events.utterance(turn.id, text, decision.decisionId);
    this.prepareOutput(turn.id);
    const result = await voiceRequest(this.bridge, {
      type: "commit", sessionId, utteranceId: turn.id, decisionId: decision.decisionId,
    });
    if (!refusalOf(result)) {
      const asked = decision.action === "clarify" || /\?\s*$/u.test(decision.say);
      this.answerExpectedUntil = asked ? performance.now() + answerWindowMs : -Infinity;
      return;
    }
    // The coding turn changed between deciding and acting: decide once more for the turn as it is now.
    const again = retried ? undefined : await this.decideFinal(sessionId, turn, text);
    if (again && this.isCaptureCurrent(turn.captureGeneration)) {
      await this.commitWith(sessionId, turn, text, again, true);
      return;
    }
    this.keepForReview(turn, text);
    this.events.error("The coding turn changed before this message could be sent. Review the transcript and try again.");
  }

  private commitFailed(turn: SpokenTurn, text: string, error: unknown) {
    if (this.closed) return;
    this.keepForReview(turn, text, "failed");
    this.events.error(messageOf(error, "That spoken message could not be sent."));
  }

  private keepForReview(turn: SpokenTurn, transcript: string, status?: VoiceReview["status"]) {
    if (this.closed || !spokenWordCount(transcript)) return;
    this.reviews.set(turn.id, { turn, draft: { id: turn.id, transcript, status } });
    this.publishReviews();
  }

  private publishReviews() {
    if (!this.closed) this.events.state({ reviews: Array.from(this.reviews.values(), review => review.draft) });
  }

  discardReview(id: string) {
    if (this.reviews.delete(id)) this.publishReviews();
  }

  sendReviewed(id: string) {
    const review = this.reviews.get(id);
    const sessionId = this.sessionId;
    if (!review || !sessionId || this.closed || this.spoken || review.draft.status) return;
    const { turn, draft: { transcript } } = review;
    this.keepForReview(turn, transcript, "sending");
    this.latestInput = id;
    void this.enqueue(async () => {
      await turn.snapshot;
      if (this.closed) return;
      await this.submit(sessionId, id, transcript, "auto");
      this.discardReview(id);
    }).catch(error => {
      if (this.closed) return;
      this.keepForReview(turn, transcript, "failed");
      this.events.error(messageOf(error, "That transcript could not be sent. You can edit it in the composer."));
    });
  }

  private consumePlaybackControl(turn: SpokenTurn, text: string) {
    if (!this.isCapturedPlaybackControl(turn, text)) return false;
    this.stopForWords(turn, text);
    return true;
  }

  private isCapturedPlaybackControl(turn: SpokenTurn, text: string) {
    return isPlaybackControl(text, turn.playback !== undefined) && !isPlaybackEcho(text, turn.playback);
  }

  /** Real words over a reply: it stops for good (its text stays on screen). */
  private stopForWords(turn: SpokenTurn, text: string) {
    if (turn.pausedReply) this.trace("stopped_for_words", { utteranceId: turn.id, text: text.slice(0, 200) });
    turn.pausedReply = false;
    this.stopCapturedPlayback(turn);
  }

  private stopCapturedPlayback(turn: SpokenTurn) {
    const sessionId = this.sessionId;
    if (!sessionId || this.latestInput !== turn.id || !turn.playbackTarget || this.outputTarget !== turn.playbackTarget) return;
    void this.interrupt(sessionId, "spoke_over_reply", turn.onsetAt).catch(() => {});
  }

  private finishSpeech(pcm: ArrayBuffer) {
    const turn = this.spoken;
    const sessionId = this.sessionId;
    this.clearCapture(false);
    if (!turn || !sessionId) return;
    const current = () => this.isCaptureCurrent(turn.captureGeneration);
    void this.decode(sessionId, turn, pcm, true, current).then(result => {
      // An empty final segment (a send just after a rollover) still carries the rolled-over speech.
      const heard = typeof result?.text === "string" ? result.text : "";
      if (!current() || this.consumePlaybackControl(turn, heard)) return;
      const text = joined([...turn.committed, spokenMessage(heard, turn.playback)]);
      if (text) this.finalize(turn, text);
      else if (!heard.trim() && turn.playback !== undefined) this.keepForReview(turn, turn.partialText || "");
    }).catch(error => {
      if (!current()) return;
      if (turn.playback !== undefined) this.keepForReview(turn, turn.partialText || "");
      this.events.error(messageOf(error, "That spoken message could not be transcribed."));
    });
  }

  submitText(text: string, intent: VoiceIntent): Promise<void> {
    const sessionId = this.sessionId;
    if (!sessionId || this.closed) return Promise.reject(new Error("Start a voice session before sending."));
    const id = crypto.randomUUID();
    this.latestInput = id;
    void this.interrupt(sessionId, "typed_message").catch(() => {});
    const snapshot = this.snapshot(sessionId, id);
    return this.enqueue(async () => {
      if (this.closed) return;
      await snapshot;
      if (!this.closed) await this.submit(sessionId, id, text, intent);
    });
  }

  private enqueue(operation: () => Promise<void>): Promise<void> {
    this.pending += 1;
    this.events.state({ pending: this.pending });
    const result = this.queue.then(operation).finally(() => {
      this.pending -= 1;
      if (!this.closed) this.events.state({ pending: this.pending });
    });
    this.queue = result.catch(() => {});
    return result;
  }

  /** A voice-started task finished and its narration is coming: give it the speaker, unless
   * the user is talking (the answer is on screen either way). */
  private narrate(utteranceId: string) {
    if (this.spoken || this.holding) {
      this.trace("narration_skipped", { utteranceId, reason: "user speaking" });
      return;
    }
    if (this.audio.playing) {
      this.trace("interrupt", { reason: "narration", wasPlaying: true });
      this.audio.stopPlayback();
    }
    this.outputTarget = utteranceId;
    this.playbackText = "";
    this.lastPlaybackAt = -Infinity;
    this.echoGate.reset();
    this.trace("narrating", { utteranceId });
  }

  /** The newest input owns the speaker: stop older playback and route this reply's audio. */
  private prepareOutput(utteranceId: string) {
    if (this.latestInput !== utteranceId) return;
    if (this.audio.playing) {
      this.trace("interrupt", { reason: "new_reply", wasPlaying: true });
      this.audio.stopPlayback();
    }
    this.outputTarget = utteranceId;
    this.playbackText = "";
    this.lastPlaybackAt = -Infinity;
    this.echoGate.reset();
  }

  private async submit(sessionId: string, utteranceId: string, text: string, intent: VoiceIntent) {
    this.events.utterance(utteranceId, text);
    this.prepareOutput(utteranceId);
    try {
      await voiceRequest(this.bridge, { type: "submit", sessionId, utteranceId, text, intent });
    } catch (error) {
      if (this.outputTarget === utteranceId) this.outputTarget = undefined;
      throw error;
    }
  }

  private receive(event: VoiceEvent) {
    if (this.closed || !this.sessionId || event.sessionId !== this.sessionId) return;
    switch (event.type) {
      case "audio":
        if (event.utteranceId === this.outputTarget) this.audio.play(event.pcm, event.sampleRate);
        break;
      case "reply":
        if (event.utteranceId === this.outputTarget) this.playbackText = event.text;
        this.events.reply(event.utteranceId, event.text, event.final);
        break;
      case "narrating":
        this.narrate(event.utteranceId);
        break;
      case "speaking":
        // What the speaker is saying: the echo filter's reference for this reply.
        if (event.utteranceId === this.outputTarget) {
          this.playbackText = this.playbackText ? `${this.playbackText} ${event.text}` : event.text;
        }
        break;
      case "receipt":
        this.events.receipt(event.receipt);
        break;
      case "error":
        this.events.error(event.message);
        break;
      case "ended":
        this.close(false);
        this.events.ended(event.reason);
        break;
    }
  }

  close(stopHost = true) {
    if (this.closed) return;
    this.closed = true;
    this.unsubscribe?.();
    this.audio.close();
    this.segments.reset();
    this.spoken = undefined;
    const sessionId = this.sessionId;
    this.sessionId = undefined;
    if (stopHost && sessionId) void voiceRequest(this.bridge, { type: "stop", sessionId }).catch(() => {});
  }
}
