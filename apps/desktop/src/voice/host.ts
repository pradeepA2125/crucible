import { randomUUID } from "node:crypto";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import type {
  ConversationIntent, ConversationReceipt, ConversationRequest, VoiceCommitResult, VoiceDecideRequest, VoiceDecision,
} from "@crucible/editor-client";
import { VoiceAssets, type VoiceModel } from "./assets.js";
import type { VoiceRequest } from "./protocol.js";
import { firstSentences, speakable } from "./speakable.js";
import { Detector, helperPath, helperUsable, Recognizer, Synthesizer } from "./workers.js";
import type { DiagnosticSource, DiagnosticValue } from "./diagnostics.js";

export interface VoiceSnapshot {
  active: boolean;
  turnId: string | null;
  processId: string | null;
}

export interface VoiceThread {
  current(): boolean;
  snapshot(signal: AbortSignal): Promise<VoiceSnapshot>;
  submit(request: ConversationRequest, signal: AbortSignal): Promise<ConversationReceipt>;
  receipt(id: string, signal: AbortSignal): Promise<ConversationReceipt | null>;
  sendIdle(text: string, voiceRequest: string | undefined, hooks: IdleHooks): Promise<void>;
  decide(request: VoiceDecideRequest, signal: AbortSignal): Promise<VoiceDecision>;
  commit(decisionId: string, expectedProcessId: string, signal: AbortSignal): Promise<VoiceCommitResult>;
  narrate(heard: string, answer: string, expectedProcessId: string, signal: AbortSignal,
    voiceModel?: string): Promise<string>;
  /** The project's own names (index snapshot), to prime speech recognition. */
  vocabulary(signal: AbortSignal): Promise<string[]>;
}

// Project words are best effort: a session never waits longer than this for them.
const VOCABULARY_TIMEOUT_MS = 1500;

type IdleHooks = { chunk(text: string): void; finish(): void; recover(): void };
type Identity = { sessionId: string; utteranceId: string };
type Utterance = { snapshot?: Promise<VoiceSnapshot>; submitted: boolean; decisions: Map<string, VoiceDecision> };
type Reply = { id: string; utteranceId: string; cancel: AbortController; text: string;
  spoken: number; output: Promise<void>; heard: string; processId: string;
  /** Stops what this reply is saying without ending it (a running task outlives its speech). */
  speech: AbortController };
type Refusal = "turn_active" | "turn_not_active" | "voice_agent_unavailable";

/** The backend's structured refusal code on an editor-client error, if any. */
/** The backend's own reason (a provider sign-in or usage message), capped for display. */
function refusalMessage(error: unknown): string | undefined {
  const detail = typeof error === "object" && error !== null ? (error as { detail?: unknown }).detail : undefined;
  const message = typeof detail === "object" && detail !== null ? (detail as { message?: unknown }).message : undefined;
  return typeof message === "string" && message.trim() ? message.trim().slice(0, 300) : undefined;
}

function refusalOf(error: unknown): Refusal | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const { status, detail } = error as { status?: unknown; detail?: unknown };
  if (status === 503) return "voice_agent_unavailable";
  const code = typeof detail === "object" && detail !== null ? (detail as { code?: unknown }).code : undefined;
  return status === 409 && (code === "turn_active" || code === "turn_not_active") ? code : undefined;
}
type Session = {
  id: string;
  thread: VoiceThread;
  signal: AbortController;
  model: VoiceModel;
  tts: "system" | "pocket";
  /** The voice model the user picked; undefined lets the backend choose per provider. */
  voiceModel: string | undefined;
  recognizer: Recognizer;
  synthesizer: Synthesizer;
  /** Silero VAD; undefined when its model is missing or it failed to start (loudness instead). */
  detector: Detector | undefined;
  utterances: Map<string, Utterance>;
  receipts: number;
  reply: Reply | undefined;
  /** The voice-started task still running: its answer streams in and is narrated at the end,
   *  whatever is said in between (a status question used to cancel its narration). */
  task: Reply | undefined;
};

type Options = {
  profile: string;
  helpers: string;
  platform: string;
  architecture: string;
  thread(id: string): Promise<VoiceThread>;
  publish(event: Record<string, unknown>): void;
  diagnostics?: { write(source: DiagnosticSource, event: string, data: Record<string, DiagnosticValue>): void };
};

const unavailable = "Local voice is available on Apple Silicon builds with the speech helpers included.";
const changedTurn = "The coding turn changed before this message could be sent. Review the transcript and try again.";
const limitSpeech = 3500;

export class VoiceHost {
  private readonly assets: VoiceAssets;
  private startup: AbortController | undefined;
  private session: Session | undefined;
  private stopping: Promise<void> | undefined;

  constructor(private readonly options: Options) {
    this.assets = new VoiceAssets(join(options.profile, "voice", "models"));
  }

  get canUseMicrophone(): boolean {
    return !!this.session?.thread.current() && !this.session.signal.signal.aborted;
  }

  async status(): Promise<{ available: boolean; reason?: string; installed: VoiceModel[];
    tts: ("system" | "pocket")[]; pocketAvailable: boolean }> {
    const installed = await this.assets.installed();
    if (this.options.platform !== "darwin" || this.options.architecture !== "arm64") {
      return { available: false, reason: unavailable, installed, tts: [], pocketAvailable: false };
    }
    const [whisper, system, pocket] = await Promise.all([
      this.helperExists("crucible-whisper"), this.helperExists("crucible-tts"),
      this.helperExists("crucible-pocket-tts"),
    ]);
    const tts: ("system" | "pocket")[] = [];
    if (system) tts.push("system");
    if (pocket && await this.assets.pocketInstalled()) tts.push("pocket");
    const available = whisper && (system || pocket);
    return { available, ...(available ? {} : { reason: unavailable }), installed, tts, pocketAvailable: pocket };
  }

  async request(request: VoiceRequest): Promise<unknown> {
    switch (request.type) {
      case "status": return this.status();
      case "cancelInstall": this.assets.cancel(); return {};
      case "install": {
        const status = await this.status();
        if (!status.available || (request.model === "pocket" && !status.pocketAvailable)) throw new Error(unavailable);
        await this.assets.install(request.model);
        return this.status();
      }
      case "remove": {
        if (this.startup || this.session?.model === request.model || (request.model === "pocket" && this.session?.tts === "pocket")) throw new Error("End the voice session before removing its model.");
        await this.assets.remove(request.model);
        return this.status();
      }
      case "start": return this.start(request.threadId, request.stt, request.tts, request.voiceModel ?? undefined);
      case "stop": {
        if (this.session?.id === request.sessionId) await this.end();
        return {};
      }
    }
    const session = this.requireSession(request.sessionId);
    switch (request.type) {
      case "interrupt": this.interrupt(session); return {};
      case "voiceModel": session.voiceModel = request.model ?? undefined; return {};
      case "snapshot": this.snapshot(session, request.utteranceId); return {};
      case "decode": return this.decode(session, request, request.pcm, request.final);
      case "submit": return this.submit(session, request.utteranceId, request.text, request.intent);
      case "decide": return this.decide(session, request.utteranceId, request.text, request.silenceMs, request.finalCall);
      case "commit": return this.commit(session, request.utteranceId, request.decisionId);
      case "trace": this.trace("app", request.event, request.data); return {};
      case "vad": return this.detect(session, request.pcm);
    }
  }

  async end(reason?: string): Promise<void> {
    this.startup?.abort();
    const session = this.session;
    if (!session) return this.stopping;
    this.session = undefined;
    this.interrupt(session);
    session.signal.abort();
    const stopped = Promise.all([session.recognizer.close(), session.synthesizer.close(),
      session.detector?.close()]).then(() => {});
    this.stopping = stopped;
    this.options.publish({ type: "ended", sessionId: session.id, ...(reason ? { reason } : {}) });
    try { await stopped; }
    finally { if (this.stopping === stopped) this.stopping = undefined; }
  }

  async dispose(): Promise<void> {
    await Promise.all([this.end(), this.assets.cancelAndWait()]);
  }

  private helper(name: string): string {
    return helperPath(this.options.helpers, name, this.options.platform);
  }

  private helperExists(name: string): Promise<boolean> {
    return helperUsable(this.helper(name), this.options.platform);
  }

  private async start(threadId: string, model: VoiceModel, tts: "system" | "pocket", voiceModel: string | undefined) {
    if (this.startup || this.session) throw new Error("A voice session is already open.");
    const abort = new AbortController();
    this.startup = abort;
    let recognizer: Recognizer | undefined;
    let synthesizer: Synthesizer | undefined;
    try {
      await this.stopping;
      const availability = await this.status();
      if (!availability.available || !availability.tts.includes(tts)) throw new Error(unavailable);
      const [thread, modelPath, pocketPath] = await Promise.all([
        this.options.thread(threadId), this.assets.verifiedPath(model, abort.signal),
        tts === "pocket" ? this.assets.verifiedPocketPath(abort.signal) : undefined,
      ]);
      abort.signal.throwIfAborted();
      if (!thread.current()) throw new Error("The conversation changed. Open voice again in the intended conversation.");
      const failed = (error: Error) => {
        if (this.session?.signal === abort) this.end(error.message);
        else abort.abort(error);
      };
      const words = await thread.vocabulary(AbortSignal.any([abort.signal, AbortSignal.timeout(VOCABULARY_TIMEOUT_MS)]))
        .catch(error => {
          this.trace("host", "vocabulary", { fallback: true, reason: error instanceof Error ? error.message : null });
          return [] as string[];
        });
      abort.signal.throwIfAborted();
      recognizer = new Recognizer(this.helper("crucible-whisper"), modelPath, failed,
        words.length ? words.join(", ") : undefined);
      synthesizer = new Synthesizer(this.helper(tts === "system" ? "crucible-tts" : "crucible-pocket-tts"),
        tts === "system" ? ["--engine", "system"] : ["--models", pocketPath!], failed);
      const synth = synthesizer;
      await Promise.all([
        recognizer.wait(abort.signal),
        synth.wait(abort.signal).then(async () => {
          if (tts === "pocket") await synth.speak("Voice ready.", () => {}, abort.signal);
        }),
      ]);
      abort.signal.throwIfAborted();
      if (!thread.current()) throw new Error("The conversation changed. Open voice again in the intended conversation.");
      const detector = await this.startDetector(abort.signal);
      const sessionId = randomUUID();
      this.session = { id: sessionId, thread, signal: abort, model, tts, voiceModel, recognizer, synthesizer, detector,
        utterances: new Map(), receipts: 0, reply: undefined, task: undefined };
      return { sessionId, vad: detector !== undefined };
    } catch (error) {
      await Promise.all([recognizer?.close(), synthesizer?.close()]);
      throw error;
    } finally { this.startup = undefined; }
  }

  /** Best-effort: without a verified model or a working worker, speech detection uses loudness. */
  private async startDetector(signal: AbortSignal): Promise<Detector | undefined> {
    let detector: Detector | undefined;
    try {
      const path = await this.assets.verifiedVadPath(signal);
      detector = new Detector(this.helper("crucible-whisper"), path, error =>
        this.trace("host", "vad", { available: false, reason: error.message }));
      await detector.wait(signal);
      this.trace("host", "vad", { available: true });
      return detector;
    } catch (error) {
      await detector?.close();
      signal.throwIfAborted();
      this.trace("host", "vad", { available: false, reason: error instanceof Error ? error.message : null });
      return undefined;
    }
  }

  private async detect(session: Session, pcm: ArrayBuffer): Promise<{ probs: number[] | null }> {
    const detector = session.detector;
    if (!detector?.usable) return { probs: null };
    try {
      return { probs: await detector.detect(pcm, session.signal.signal) };
    } catch {
      return { probs: null };
    }
  }

  private requireSession(id: string): Session {
    const session = this.session;
    if (!session || session.id !== id) throw new Error("This voice session has ended.");
    if (!session.thread.current()) {
      this.end("The conversation changed. Start voice again in the intended conversation.");
      throw new Error("This voice session has ended.");
    }
    return session;
  }

  private snapshot(session: Session, id: string): void {
    if (session.utterances.has(id)) return;
    if (session.utterances.size >= 32) {
      const oldest = session.utterances.keys().next().value;
      if (oldest) session.utterances.delete(oldest);
    }
    const snapshot = session.thread.snapshot(AbortSignal.any([session.signal.signal, AbortSignal.timeout(4000)]));
    void snapshot.catch(() => {});
    session.utterances.set(id, { snapshot, submitted: false, decisions: new Map() });
  }

  private async decode(session: Session, id: Identity, pcm: ArrayBuffer, _final: boolean) {
    const entry = session.utterances.get(id.utteranceId);
    if (!entry || entry.submitted) throw new Error("This spoken message is no longer open.");
    const text = await session.recognizer.decode(pcm, session.signal.signal);
    this.requireSession(id.sessionId);
    return { text };
  }

  private async submit(session: Session, id: string, text: string, intent: ConversationIntent) {
    const entry = session.utterances.get(id);
    if (!entry?.snapshot || entry.submitted) throw new Error("This voice message is no longer open.");
    entry.submitted = true;
    const snapshot = await entry.snapshot;
    const reply = this.newReply(session, id, text, snapshot.processId ?? "");
    this.requireSession(session.id);
    if (!snapshot.active) {
      if (intent === "steer") throw new Error("There was no active coding turn when this message began.");
      const current = await session.thread.snapshot(AbortSignal.any([session.signal.signal, AbortSignal.timeout(4000)]));
      this.requireSession(session.id);
      if (current.active || !snapshot.processId || snapshot.processId !== current.processId) throw new Error(changedTurn);
      this.startIdle(session, reply, text, undefined);
      return {};
    }
    if (!snapshot.turnId || !snapshot.processId) throw new Error("The runtime could not identify the active coding turn. Wait for it to reconnect.");
    const request: ConversationRequest = {
      message_id: id, text, intent, expected_turn_id: snapshot.turnId, expected_process_id: snapshot.processId,
    };
    const signal = AbortSignal.any([session.signal.signal, AbortSignal.timeout(25_000)]);
    let receipt: ConversationReceipt;
    try {
      receipt = await session.thread.submit(request, signal);
    } catch (error) {
      if (session.signal.signal.aborted) throw error;
      // A network failure may have happened after admission. Read by id, never retry the POST automatically.
      const observed = await session.thread.receipt(id, AbortSignal.any([session.signal.signal, AbortSignal.timeout(4000)])).catch(() => null);
      if (!observed) {
        if (error instanceof Error && "status" in error && error.status === 409) throw new Error(changedTurn);
        throw new Error("Crucible could not confirm this voice message. Review its transcript before trying again.");
      }
      receipt = observed;
    }
    this.requireSession(session.id);
    this.deliverReceipt(session, receipt, id, snapshot.turnId);
    this.append(session, reply, receipt.text, true);
    this.speak(session, reply, receipt.text);
    if (receipt.status === "queued") void this.watchReceipt(session, id, snapshot.turnId);
    return {};
  }

  private async decide(session: Session, id: string, text: string, silenceMs: number, finalCall: boolean) {
    const entry = session.utterances.get(id);
    if (!entry?.snapshot || entry.submitted) throw new Error("This spoken message is no longer open.");
    const snapshot = await entry.snapshot;
    this.requireSession(session.id);
    if (!snapshot.processId) throw new Error("The runtime could not identify this conversation. Wait for it to reconnect.");
    let decision: VoiceDecision;
    const started = performance.now();
    const asked = { utteranceId: id, text, silenceMs, finalCall };
    try {
      decision = await session.thread.decide({ utterance: text, silenceMs, finalCall, expectedProcessId: snapshot.processId,
        ...(session.voiceModel ? { voiceModel: session.voiceModel } : {}) },
        AbortSignal.any([session.signal.signal, AbortSignal.timeout(15_000)]));
    } catch (error) {
      const refused = refusalOf(error);
      const message = refused ? refusalMessage(error) : undefined;
      this.trace("host", "decide", { ...asked, ms: Math.round(performance.now() - started),
        refused: refused ?? null, reason: message ?? (error instanceof Error ? error.message : null) });
      if (refused) return message ? { refused, message } : { refused };
      throw error;
    }
    this.trace("host", "decide", { ...asked, ms: Math.round(performance.now() - started), decisionId: decision.decisionId,
      turn: decision.turn, action: decision.action, say: decision.say, request: decision.request });
    this.requireSession(session.id);
    entry.decisions.set(decision.decisionId, decision);
    const { decisionId, turn, action, say, request } = decision;
    return { decisionId, turn, action, say, request };
  }

  private async commit(session: Session, id: string, decisionId: string) {
    const entry = session.utterances.get(id);
    const decision = entry?.decisions.get(decisionId);
    if (!entry?.snapshot || !decision || entry.submitted) throw new Error("This voice message is no longer open.");
    entry.submitted = true;
    const { processId } = await entry.snapshot;
    this.requireSession(session.id);
    let result: VoiceCommitResult;
    try {
      if (!processId) throw new Error("The runtime could not identify this conversation. Wait for it to reconnect.");
      result = await session.thread.commit(decisionId, processId, AbortSignal.any([session.signal.signal, AbortSignal.timeout(10_000)]));
    } catch (error) {
      const refused = refusalOf(error);
      this.trace("host", "commit", { utteranceId: id, decisionId, refused: refused ?? null,
        reason: error instanceof Error ? error.message : null });
      if (refused === "turn_active" || refused === "turn_not_active") {
        // Nothing happened: the renderer may decide again for the turn as it is now.
        entry.submitted = false;
        entry.decisions.delete(decisionId);
        return { refused };
      }
      throw error;
    }
    this.requireSession(session.id);
    entry.decisions.delete(decisionId);
    this.trace("host", "commit", { utteranceId: id, decisionId, action: result.action });
    if (result.action === "listen") {
      entry.submitted = false;
      return { action: result.action };
    }
    // `heard` feeds narration, which only a started task reaches.
    const reply = this.newReply(session, id, result.action === "start_task" ? result.message : "", processId);
    switch (result.action) {
      case "reply":
      case "clarify":
        this.append(session, reply, decision.say, true);
        this.speak(session, reply, decision.say);
        break;
      case "steer": {
        const turn = decision.turnId ?? result.receipt.turn_id;
        this.deliverReceipt(session, result.receipt, decisionId, turn, id);
        this.append(session, reply, decision.say, true);
        this.speak(session, reply, decision.say);
        if (result.receipt.status === "queued") void this.watchReceipt(session, decisionId, turn, id);
        break;
      }
      case "start_task":
        if (session.task && session.task !== reply) session.task.cancel.abort();
        session.task = reply;
        // The acknowledgement is spoken only; the panel shows the agent's own answer.
        this.speak(session, reply, decision.say);
        this.startIdle(session, reply, result.message, result.voiceRequest);
        break;
    }
    return { action: result.action };
  }

  private startIdle(session: Session, reply: Reply, text: string, voiceRequest: string | undefined): void {
    const failed = () => {
      if (session.task === reply) session.task = undefined;
      this.error(session, "The spoken message could not be sent. Review its transcript and try again.");
    };
    void session.thread.sendIdle(text, voiceRequest, {
      chunk: chunk => this.append(session, reply, chunk, false),
      finish: () => this.finishIdle(session, reply),
      recover: failed,
    }).catch(failed);
  }

  /** The written answer stays on screen; the voice agent says what matters. */
  private finishIdle(session: Session, reply: Reply): void {
    this.append(session, reply, "", true);
    const answer = reply.text.trim();
    if (!answer || !this.currentReply(session, reply)) return;
    // The task's reply takes the speaker back from any status reply, and is no longer
    // protected: interrupting the narration now cancels it.
    if (session.task === reply) session.task = undefined;
    if (session.reply !== reply) {
      this.interrupt(session);
      session.reply = reply;
    }
    this.options.publish({ type: "narrating", sessionId: session.id, utteranceId: reply.utteranceId });
    void session.thread.narrate(reply.heard, answer, reply.processId,
      AbortSignal.any([reply.cancel.signal, AbortSignal.timeout(20_000)]), session.voiceModel)
      .catch(error => {
        this.trace("host", "narrate", { fallback: true, reason: error instanceof Error ? error.message : null });
        return `${firstSentences(speakable(answer), 2)} The rest is on screen.`;
      })
      .then(say => { if (this.currentReply(session, reply)) this.speak(session, reply, say); });
  }

  /** `shownAs` is the utterance the panel shows it under: a voice correction is keyed by its
   *  decision on the backend but belongs to the spoken utterance on screen. */
  private deliverReceipt(session: Session, receipt: ConversationReceipt, id: string, turn: string,
    shownAs = id): void {
    if (receipt.message_id !== id || receipt.turn_id !== turn) throw new Error("Received a voice receipt for a different coding turn.");
    this.options.publish({ type: "receipt", sessionId: session.id, receipt: { ...receipt, message_id: shownAs } });
  }

  private async watchReceipt(session: Session, id: string, turn: string, shownAs = id): Promise<void> {
    if (session.receipts >= 16) {
      this.error(session, "Some correction receipts are pending. Check the coding conversation for updates.");
      return;
    }
    session.receipts++;
    const signal = AbortSignal.any([session.signal.signal, AbortSignal.timeout(30 * 60_000)]);
    try {
      while (!signal.aborted) {
        await delay(850, undefined, { signal });
        this.requireSession(session.id);
        const receipt = await session.thread.receipt(id, AbortSignal.any([signal, AbortSignal.timeout(4000)]));
        if (!receipt) throw new Error("Receipt not found");
        if (receipt.status !== "queued") {
          this.deliverReceipt(session, receipt, id, turn, shownAs);
          return;
        }
      }
    } catch {
      if (!session.signal.signal.aborted) this.error(session, "Crucible could not confirm whether the correction was incorporated.");
    } finally { session.receipts--; }
  }

  private newReply(session: Session, id: string, heard: string, processId: string): Reply {
    this.interrupt(session);
    const reply = { id: randomUUID(), utteranceId: id, cancel: new AbortController(), text: "",
      spoken: 0, output: Promise.resolve(), heard, processId, speech: new AbortController() };
    session.reply = reply;
    return reply;
  }

  private interrupt(session: Session): void {
    const reply = session.reply;
    session.reply = undefined;
    if (!reply) return;
    if (reply === session.task) {
      // Only stop what it is saying: the task keeps running and will still be narrated.
      reply.speech.abort();
      reply.speech = new AbortController();
      return;
    }
    reply.cancel.abort();
  }

  private append(session: Session, reply: Reply, chunk: string, final: boolean): void {
    if (!this.currentReply(session, reply)) return;
    const next = chunk.slice(0, Math.max(0, 16_000 - reply.text.length));
    reply.text += next;
    this.options.publish({ type: "reply", sessionId: session.id, utteranceId: reply.utteranceId,
      messageId: reply.id, text: reply.text, final });
  }

  private speak(session: Session, reply: Reply, segment: string): void {
    const text = speakable(segment).slice(0, limitSpeech - reply.spoken);
    if (!text) return;
    reply.spoken += text.length;
    const speech = reply.speech.signal;
    reply.output = reply.output.then(async () => {
      if (!this.currentReply(session, reply) || speech.aborted) return;
      // The app compares what the microphone hears against this, so its own voice coming
      // back (narration, "On it.") is recognised as echo instead of stopping the reply.
      this.options.publish({ type: "speaking", sessionId: session.id, utteranceId: reply.utteranceId, text });
      const started = performance.now();
      await session.synthesizer.speak(text, (pcm, sampleRate) => {
        if (this.currentReply(session, reply)) this.options.publish({ type: "audio", sessionId: session.id,
          utteranceId: reply.utteranceId, replyId: reply.id, pcm, sampleRate });
      }, AbortSignal.any([reply.cancel.signal, speech]));
      this.trace("host", "speak", { utteranceId: reply.utteranceId, chars: text.length, text,
        ms: Math.round(performance.now() - started) });
    }).catch(error => {
      this.trace("host", "speak", { utteranceId: reply.utteranceId, chars: text.length, text,
        failed: error instanceof Error ? error.message : true });
      // Speech stopped on purpose is not a failure.
      if (this.currentReply(session, reply) && !speech.aborted) {
        this.error(session, "The reply is available in text. The local voice could not read it.");
      }
    });
  }

  private trace(source: DiagnosticSource, event: string, data: Record<string, DiagnosticValue>): void {
    try {
      this.options.diagnostics?.write(source, event, data);
    } catch {
      // Diagnostics never affect the voice session.
    }
  }

  private currentReply(session: Session, reply: Reply): boolean {
    return this.session === session && (session.reply === reply || session.task === reply)
      && !reply.cancel.signal.aborted && session.thread.current();
  }

  private error(session: Session, message: string): void {
    if (this.session === session && session.thread.current()) this.options.publish({ type: "error", sessionId: session.id, message });
  }
}
