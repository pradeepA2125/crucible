import { SpeechSegments } from "./speechSegments";
import {
  clearOverlapRequest, hasConflictingReferences, hasSubstantiveSpeech, isBackchannel, isPlaybackControl, isPlaybackEcho,
  isStableTranscript, soundsUnfinished, spokenMessage, spokenWordCount,
} from "./speechText";
import { VoiceAudio } from "./voiceAudio";
import {
  voiceRequest,
  type VoiceBridge, type VoiceEvent, type VoiceIntent, type VoiceReceipt, type VoiceRecognizer, type VoiceSynthesizer,
} from "./voiceBridge";

export interface VoiceSessionState {
  connected: boolean;
  microphone: "off" | "starting" | "on";
  capturing: boolean;
  checking: boolean;
  speaking: boolean;
  pending: number;
  partial: string;
  playbackStopMs?: number;
  reviews: readonly VoiceReview[];
}

export const INITIAL_VOICE_STATE: VoiceSessionState = {
  connected: false, microphone: "off", capturing: false, checking: false, speaking: false, pending: 0, partial: "", reviews: [],
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
  revision: number;
  partialText?: string;
  check?: PauseCheck;
}

interface PauseCheck {
  text: string;
  clear: boolean;
  settled: boolean;
  briefOverlap: boolean;
  canRecheck?: boolean;
  result?: Promise<void>;
  recheck?: Promise<void>;
}

const playbackTailMs = 300;

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
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

  constructor(private readonly bridge: VoiceBridge, private readonly events: VoiceSessionCallbacks) {
    this.audio = new VoiceAudio(speaking => {
      if (!speaking) this.lastPlaybackAt = performance.now();
      if (!this.closed) events.state({ speaking });
    });
    this.segments = new SpeechSegments({
      start: onsetAt => this.beginSpeech(onsetAt),
      partial: pcm => this.decodePartial(pcm),
      pause: (pcm, cleanSpeechMs) => this.checkPause(pcm, cleanSpeechMs),
      settle: () => {
        const turn = this.spoken;
        if (!turn?.check) return;
        turn.check.settled = true;
        this.forwardChecked(turn);
      },
      recheck: pcm => this.recheckPause(pcm),
      resume: () => {
        if (this.spoken) {
          this.spoken.check = undefined;
          this.spoken.partialText = undefined;
          this.spoken.revision += 1;
        }
        events.state({ checking: false });
      },
      pauseExpired: () => this.reviewAfterPause(),
      finish: pcm => this.finishSpeech(pcm),
      discard: () => this.clearCapture(false),
      tooLong: () => {
        this.clearCapture(false);
        events.error("That message was longer than 30 seconds. Try a shorter message or type it below.");
      },
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
    if (turn && turn.playback !== undefined) this.keepForReview(turn, turn.check?.text || turn.partialText || "");
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
      playback, playbackTarget, onsetAt, revision: 0,
    };
    this.events.state({ capturing: true, partial: "" });
  }

  private clearCapture(reset = true) {
    this.spoken = undefined;
    if (reset) this.segments.reset();
    this.events.state({ capturing: false, checking: false, partial: "" });
  }

  private decode(sessionId: string, turn: SpokenTurn, pcm: ArrayBuffer, final: boolean, valid: () => boolean) {
    const result = this.decoding.then(() => valid() ? voiceRequest(this.bridge, {
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
        const partial = spokenMessage(result.text, turn.playback);
        if (this.isCapturedPlaybackControl(turn, result.text) || hasSubstantiveSpeech(partial)) {
          this.stopCapturedPlayback(turn);
        }
        if (turn.playback === undefined || spokenWordCount(partial)) turn.partialText = partial;
        if (!turn.check) this.events.state({ partial });
      }
    }).catch(() => {
      // A partial is expendable. A failed final decode is shown to the user.
    }).finally(() => {
      if (this.partialInFlight === partial) this.partialInFlight = undefined;
    });
    this.partialInFlight = partial;
  }

  private checkPause(pcm: ArrayBuffer, cleanSpeechMs: number) {
    const turn = this.spoken;
    const sessionId = this.sessionId;
    if (!turn || !sessionId) return;
    const check: PauseCheck = { text: "", clear: false, settled: false,
      briefOverlap: turn.playback !== undefined && cleanSpeechMs < 420 };
    turn.check = check;
    const current = () => this.spoken === turn && turn.check === check && this.isCaptureCurrent(turn.captureGeneration);
    this.events.state({ checking: true });

    check.result = this.decode(sessionId, turn, pcm, true, current).then(result => {
      if (!current() || typeof result?.text !== "string") return;
      if (this.consumePlaybackControl(turn, result.text)) {
        this.clearCapture();
        return;
      }
      const text = spokenMessage(result.text, turn.playback);
      if (!text || isBackchannel(text) || (turn.playback !== undefined && !spokenWordCount(text))) {
        if (!result.text.trim() && turn.playback !== undefined) this.keepForReview(turn, turn.partialText || "");
        this.clearCapture();
        return;
      }
      if (hasSubstantiveSpeech(text)) this.stopCapturedPlayback(turn);
      check.text = text;
      this.events.state({ partial: text });
      check.canRecheck = !soundsUnfinished(text) &&
        !hasConflictingReferences(turn.partialText ?? "", text) &&
        (turn.playback === undefined || (!check.briefOverlap && clearOverlapRequest(text)));
      this.events.state({ checking: false });
    }).catch(() => {
      if (current()) this.events.state({ checking: false });
    });
  }

  private recheckPause(pcm: ArrayBuffer) {
    const turn = this.spoken;
    const check = turn?.check;
    const sessionId = this.sessionId;
    if (!turn || !check || !sessionId) return;
    const current = () => this.spoken === turn && turn.check === check && this.isCaptureCurrent(turn.captureGeneration);
    check.recheck = check.result?.then(async () => {
      if (!current() || !check.canRecheck) return;
      const result = await this.decode(sessionId, turn, pcm, true, current);
      if (current() && typeof result?.text === "string" && isStableTranscript(check.text, result.text)) {
        check.clear = true;
        this.forwardChecked(turn);
      }
    }).catch(() => {});
  }

  private forwardChecked(turn: SpokenTurn) {
    const sessionId = this.sessionId;
    const check = turn.check;
    if (!sessionId || !check?.clear || !check.settled) return;
    this.clearCapture();
    this.sendSpoken(sessionId, turn, check.text);
  }

  private reviewAfterPause() {
    const turn = this.spoken;
    if (!turn) return;
    const check = turn.check;
    void Promise.resolve(check?.recheck ?? check?.result).then(() => {
      if (this.spoken !== turn || turn.check !== check || !this.isCaptureCurrent(turn.captureGeneration)) return;
      const text = check?.text || turn.partialText || "";
      this.clearCapture();
      if (this.latestInput === turn.id) this.keepForReview(turn, text);
    });
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
      const text = spokenMessage(result.text, turn.playback);
      if (text) this.sendSpoken(sessionId, turn, text, true);
      else if (!result.text.trim() && turn.playback !== undefined) this.keepForReview(turn, turn.partialText || "");
    }).catch(error => {
      if (!current()) return;
      if (turn.playback !== undefined) this.keepForReview(turn, turn.partialText || "");
      this.events.error(messageOf(error, "That spoken message could not be transcribed."));
    });
  }

  private sendSpoken(sessionId: string, turn: SpokenTurn, text: string, explicit = false) {
    const current = () => this.isCaptureCurrent(turn.captureGeneration) && (explicit || this.latestInput === turn.id);
    void this.enqueue(async () => {
      if (current()) {
        await turn.snapshot;
        if (current()) {
          await this.submit(sessionId, turn.id, text, "auto");
          return;
        }
      }
      if (turn.playback !== undefined) this.keepForReview(turn, text);
    }).catch(error => {
      if (turn.playback !== undefined) this.keepForReview(turn, text, "failed");
      if (!this.closed && (current() || turn.playback !== undefined)) {
        this.events.error(messageOf(error, "That spoken message could not be sent."));
      }
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

  private async submit(sessionId: string, utteranceId: string, text: string, intent: VoiceIntent) {
    this.events.utterance(utteranceId, text);
    if (this.latestInput === utteranceId) {
      if (this.audio.playing) this.audio.stopPlayback();
      this.outputTarget = utteranceId;
      this.playbackText = "";
      this.lastPlaybackAt = -Infinity;
    }
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
