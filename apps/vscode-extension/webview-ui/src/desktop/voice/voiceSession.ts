import { SpeechSegments } from "./speechSegments";
import {
  clearOverlapRequest, hasSubstantiveSpeech, isBackchannel, isPlaybackControl, isPlaybackEcho, spokenMessage,
  spokenWordCount,
} from "./speechText";
import { DISPATCH_HOLD_MS, commitFloorMs } from "./turnTiming";
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
  utterance(id: string, text: string): void;
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
}

const playbackTailMs = 300;
// After the assistant asks a question, a one-word answer ("okay", "yes") is a real reply.
const answerWindowMs = 15_000;
const UNAVAILABLE = "The voice assistant could not decide what to do. Review the transcript and send it.";
// A decision still out after this long gets a quiet cue, so silence never reads as "not heard".
const SLOW_DECISION_MS = 1_200;
// The cue's own sound must not count as the user speaking again (one loud frame resumes speech).
const CUE_MASK_TAIL_MS = 200;

export type VoiceTraceValue = string | number | boolean | null;
/** Where diagnostics go (the desktop host's local log). Never affects the session. */
export type VoiceTrace = (sessionId: string, event: string, data: Record<string, VoiceTraceValue>) => void;

const LEVELS_IN_TURN_FRAMES = 50; // one line per second while a turn is open
const LEVELS_IDLE_FRAMES = 250; // one per five seconds otherwise
const round = (value: number) => Math.round(value * 10_000) / 10_000;

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
  private lastRms = 0;
  private lastPartialTraced = "";
  private meter = { frames: 0, peak: 0, sum: 0, loud: 0 };

  constructor(private readonly bridge: VoiceBridge, private readonly events: VoiceSessionCallbacks,
    private readonly traceSink?: VoiceTrace) {
    this.audio = new VoiceAudio(speaking => {
      if (!speaking) this.lastPlaybackAt = performance.now();
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

  async start(threadId: string, stt: VoiceRecognizer, tts: VoiceSynthesizer) {
    void this.audio.resume().catch(() => {});
    this.unsubscribe = this.bridge.subscribe(event => this.receive(event));
    const started = await voiceRequest(this.bridge, { type: "start", threadId, stt, tts });
    if (typeof started.sessionId !== "string" || !started.sessionId) {
      throw new Error("Crucible could not start a voice session.");
    }
    if (this.closed) {
      void voiceRequest(this.bridge, { type: "stop", sessionId: started.sessionId }).catch(() => {});
      return;
    }
    this.sessionId = started.sessionId;
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
          const cueSounding = performance.now() < this.cueMaskUntil;
          this.lastRms = frame.rms;
          this.measure(frame.rms, cueSounding, playing);
          this.segments.accept(cueSounding ? { ...frame, rms: 0 } : frame, playing, !this.partialInFlight && (this.pending === 0 || playing), this.pushToTalk,
            !playing && performance.now() - this.lastPlaybackAt > playbackTailMs);
          this.commitWhenQuietEnough();
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
    void this.interrupt(this.sessionId, performance.now()).catch(error => {
      if (!this.closed) this.events.error(messageOf(error, "Could not stop the spoken reply."));
    });
  }

  private isCaptureCurrent(generation: number) {
    return !this.closed && this.captureGeneration === generation;
  }

  private interrupt(sessionId: string, startedAt = performance.now()) {
    this.outputTarget = undefined;
    const stoppedAt = this.audio.stopPlayback();
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

  /** Microphone levels against the live thresholds: the evidence for "why did it (not) pause". */
  private measure(rms: number, masked: boolean, playing: boolean) {
    if (!Number.isFinite(rms)) return;
    const meter = this.meter;
    const levels = this.segments.levels;
    meter.frames += 1;
    meter.peak = Math.max(meter.peak, rms);
    meter.sum += rms;
    if (rms >= levels.continueAt) meter.loud += 1;
    if (meter.frames < (levels.inTurn ? LEVELS_IN_TURN_FRAMES : LEVELS_IDLE_FRAMES)) return;
    this.trace("levels", {
      peak: round(meter.peak), mean: round(meter.sum / meter.frames), loudFrames: meter.loud, frames: meter.frames,
      noise: round(levels.noise), continueAt: round(levels.continueAt), onsetAt: round(levels.onsetAt),
      silenceMs: this.segments.silenceMs, inTurn: levels.inTurn, paused: levels.paused, playing, masked,
    });
    this.meter = { frames: 0, peak: 0, sum: 0, loud: 0 };
  }

  private beginSpeech(onsetAt: number) {
    const sessionId = this.sessionId;
    if (!sessionId || this.closed) return;
    const id = crypto.randomUUID();
    const playback = this.audio.playing || onsetAt - this.lastPlaybackAt <= playbackTailMs ? this.playbackText : undefined;
    const playbackTarget = playback === undefined ? undefined : this.outputTarget;
    this.latestInput = id;
    this.trace("onset", { utteranceId: id, overPlayback: playback !== undefined, rms: round(this.lastRms) });
    this.spoken = {
      id, captureGeneration: this.captureGeneration, snapshot: this.snapshot(sessionId, id),
      playback, playbackTarget, onsetAt, revision: 0, committed: [],
    };
    this.events.state({ capturing: true, partial: "" });
  }

  private clearCapture(reset = true) {
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
    if (!turn || !sessionId || this.partialInFlight || this.closed) return;
    const revision = turn.revision;
    const current = () => this.spoken === turn && turn.revision === revision && this.isCaptureCurrent(turn.captureGeneration);
    const partial = this.decode(sessionId, turn, pcm, false, current).then(result => {
      if (current() && typeof result?.text === "string") {
        const partial = joined([...turn.committed, spokenMessage(result.text, turn.playback)]);
        if (this.isCapturedPlaybackControl(turn, result.text) || hasSubstantiveSpeech(partial)) {
          this.stopCapturedPlayback(turn);
        }
        if (turn.playback === undefined || spokenWordCount(partial)) turn.partialText = partial;
        if (partial !== this.lastPartialTraced) {
          this.lastPartialTraced = partial;
          this.trace("transcript", { utteranceId: turn.id, final: false, text: partial.slice(0, 1000) });
        }
        if (turn.deciding === undefined && !turn.decision) this.events.state({ partial });
      }
    }).catch(() => {
      // A partial is expendable. A failed final decode is shown to the user.
    }).finally(() => {
      if (this.partialInFlight === partial) this.partialInFlight = undefined;
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
    turn.decodingPause = revision;
    this.events.state({ checking: true });
    void this.decode(sessionId, turn, pcm, true, current).then(result => {
      const heard = typeof result?.text === "string" ? result.text : "";
      this.trace("transcript", { utteranceId: turn.id, final: true, text: heard.slice(0, 1000), stale: !current(),
        revision });
      if (!current()) return;
      turn.decodingPause = undefined;
      if (this.consumePlaybackControl(turn, heard)) {
        this.clearCapture();
        return;
      }
      const answering = performance.now() < this.answerExpectedUntil;
      const text = joined([...turn.committed, spokenMessage(heard, turn.playback)]);
      if (!text || (!answering && isBackchannel(text)) || (turn.playback !== undefined && !spokenWordCount(text))) {
        if (!heard.trim() && turn.playback !== undefined) this.keepForReview(turn, turn.partialText || "");
        this.clearCapture();
        return;
      }
      if (hasSubstantiveSpeech(text)) this.stopCapturedPlayback(turn);
      turn.text = text;
      this.events.state({ partial: text });
      // A short reply to playback can be misheard as an instruction; it waits for review.
      turn.held = turn.playback !== undefined && (cleanSpeechMs < 420 || !clearOverlapRequest(text));
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
      utteranceId: turn?.id ?? null, quietMs: silenceMs, rms: round(this.lastRms),
      threshold: round(this.segments.levels.continueAt),
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
    this.events.utterance(turn.id, text);
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
    this.stopCapturedPlayback(turn);
    return true;
  }

  private isCapturedPlaybackControl(turn: SpokenTurn, text: string) {
    return isPlaybackControl(text, turn.playback !== undefined) && !isPlaybackEcho(text, turn.playback);
  }

  private stopCapturedPlayback(turn: SpokenTurn) {
    const sessionId = this.sessionId;
    if (!sessionId || this.latestInput !== turn.id || !turn.playbackTarget || this.outputTarget !== turn.playbackTarget) return;
    void this.interrupt(sessionId, turn.onsetAt).catch(() => {});
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
    void this.interrupt(sessionId).catch(() => {});
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

  /** The newest input owns the speaker: stop older playback and route this reply's audio. */
  private prepareOutput(utteranceId: string) {
    if (this.latestInput !== utteranceId) return;
    if (this.audio.playing) this.audio.stopPlayback();
    this.outputTarget = utteranceId;
    this.playbackText = "";
    this.lastPlaybackAt = -Infinity;
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
