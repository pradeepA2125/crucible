import { SpeechSegments } from "./speechSegments";
import {
  clearOverlapRequest, hasSubstantiveSpeech, isBackchannel, isPlaybackControl, isPlaybackEcho, spokenMessage,
  spokenWordCount,
} from "./speechText";
import { DISPATCH_HOLD_MS, commitFloorMs } from "./turnTiming";
import { VoiceAudio } from "./voiceAudio";
import {
  decisionOf, refusalOf, voiceRequest,
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
  speaking: boolean;
  pending: number;
  partial: string;
  playbackStopMs?: number;
  reviews: readonly VoiceReview[];
}

export const INITIAL_VOICE_STATE: VoiceSessionState = {
  connected: false, microphone: "off", capturing: false, checking: false, waiting: false, speaking: false, pending: 0,
  partial: "", reviews: [],
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
  finalWanted?: boolean;
  decision?: VoiceDecision & { revision: number };
}

const playbackTailMs = 300;
const UNAVAILABLE = "The voice assistant could not decide what to do. Review the transcript and send it.";

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

  constructor(private readonly bridge: VoiceBridge, private readonly events: VoiceSessionCallbacks) {
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
          this.segments.accept(frame, playing, !this.partialInFlight && (this.pending === 0 || playing), this.pushToTalk,
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

  private beginSpeech(onsetAt: number) {
    const sessionId = this.sessionId;
    if (!sessionId || this.closed) return;
    const id = crypto.randomUUID();
    const playback = this.audio.playing || onsetAt - this.lastPlaybackAt <= playbackTailMs ? this.playbackText : undefined;
    const playbackTarget = playback === undefined ? undefined : this.outputTarget;
    this.latestInput = id;
    this.spoken = {
      id, captureGeneration: this.captureGeneration, snapshot: this.snapshot(sessionId, id),
      playback, playbackTarget, onsetAt, revision: 0, committed: [],
    };
    this.events.state({ capturing: true, partial: "" });
  }

  private clearCapture(reset = true) {
    this.spoken = undefined;
    if (reset) this.segments.reset();
    this.events.state({ capturing: false, checking: false, waiting: false, partial: "" });
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
    this.events.state({ checking: true });
    void this.decode(sessionId, turn, pcm, true, current).then(result => {
      if (!current()) return;
      const heard = typeof result?.text === "string" ? result.text : "";
      if (this.consumePlaybackControl(turn, heard)) {
        this.clearCapture();
        return;
      }
      const text = joined([...turn.committed, spokenMessage(heard, turn.playback)]);
      if (!text || isBackchannel(text) || (turn.playback !== undefined && !spokenWordCount(text))) {
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
        return;
      }
      this.requestDecision(turn, false);
    }).catch(() => {
      if (current()) this.events.state({ checking: false });
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
    this.events.state({ checking: true });
    void voiceRequest(this.bridge, {
      type: "decide", sessionId, utteranceId: turn.id, text, silenceMs: this.segments.silenceMs, finalCall,
    }).then(result => {
      if (!current()) return;
      turn.deciding = undefined;
      this.events.state({ checking: false });
      const decision = decisionOf(result);
      if (refusalOf(result) || !decision) {
        this.unavailable(turn, text);
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
    }).catch(() => {
      if (!current()) return;
      turn.deciding = undefined;
      this.unavailable(turn, text);
    });
  }

  private unavailable(turn: SpokenTurn, text: string) {
    this.clearCapture();
    this.keepForReview(turn, text);
    this.events.error(UNAVAILABLE);
  }

  /** Called on every microphone frame: acts once the speaker has been quiet long enough. */
  private commitWhenQuietEnough() {
    const turn = this.spoken;
    const decision = turn?.decision;
    if (!turn || !decision || decision.revision !== turn.revision) return;
    const required = commitFloorMs(this.resumedPauses) + (waitsForMore(decision) ? DISPATCH_HOLD_MS : 0);
    if (this.segments.silenceMs >= required) this.commitDecision(turn);
  }

  private resumeSpeech(silenceMs: number) {
    if (silenceMs >= 400) {
      this.resumedPauses.push(silenceMs);
      if (this.resumedPauses.length > 50) this.resumedPauses.shift();
    }
    const turn = this.spoken;
    if (turn) {
      turn.revision += 1;
      turn.decision = undefined;
      turn.deciding = undefined;
      turn.finalWanted = false;
      turn.partialText = undefined;
    }
    this.events.state({ checking: false, waiting: false });
  }

  private reachWaitLimit() {
    const turn = this.spoken;
    if (!turn || turn.decision) return;
    if (turn.held || !turn.text) {
      const text = turn.text || turn.partialText || "";
      this.clearCapture();
      if (turn.held && this.latestInput === turn.id) this.keepForReview(turn, text);
      return;
    }
    if (turn.deciding !== undefined) turn.finalWanted = true;
    else this.requestDecision(turn, true);
  }

  private rollOver(pcm: ArrayBuffer) {
    const turn = this.spoken;
    const sessionId = this.sessionId;
    if (!turn || !sessionId) return;
    // Survives resumed speech: this audio is final whatever the speaker says next.
    const current = () => this.spoken === turn && this.isCaptureCurrent(turn.captureGeneration);
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
      await this.commitWith(sessionId, turn, text, decision.decisionId, false);
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
      await this.commitWith(sessionId, turn, text, decided.decisionId, false);
    }).catch(error => this.commitFailed(turn, text, error));
  }

  private async decideFinal(sessionId: string, turn: SpokenTurn, text: string): Promise<VoiceDecision | undefined> {
    const result = await voiceRequest(this.bridge, {
      type: "decide", sessionId, utteranceId: turn.id, text, silenceMs: this.segments.silenceMs, finalCall: true,
    });
    const decision = decisionOf(result);
    return refusalOf(result) || !decision || decision.action === "listen" ? undefined : decision;
  }

  private async commitWith(sessionId: string, turn: SpokenTurn, text: string, decisionId: string, retried: boolean) {
    this.events.utterance(turn.id, text);
    this.prepareOutput(turn.id);
    const result = await voiceRequest(this.bridge, { type: "commit", sessionId, utteranceId: turn.id, decisionId });
    if (!refusalOf(result)) return;
    // The coding turn changed between deciding and acting: decide once more for the turn as it is now.
    const again = retried ? undefined : await this.decideFinal(sessionId, turn, text);
    if (again && this.isCaptureCurrent(turn.captureGeneration)) {
      await this.commitWith(sessionId, turn, text, again.decisionId, true);
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
      if (!current() || typeof result?.text !== "string" || this.consumePlaybackControl(turn, result.text)) return;
      const text = joined([...turn.committed, spokenMessage(result.text, turn.playback)]);
      if (text) this.finalize(turn, text);
      else if (!result.text.trim() && turn.playback !== undefined) this.keepForReview(turn, turn.partialText || "");
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
