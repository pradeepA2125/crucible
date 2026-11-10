import { randomUUID } from "node:crypto";
import { access, lstat } from "node:fs/promises";
import { constants } from "node:fs";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import type {
  ConversationIntent, ConversationReceipt, ConversationRequest, VoiceCommitResult, VoiceDecideRequest, VoiceDecision,
} from "@crucible/editor-client";
import { VoiceAssets, type VoiceModel } from "./assets.js";
import type { VoiceRequest } from "./protocol.js";
import { firstSentences, speakable } from "./speakable.js";
import { Recognizer, Synthesizer } from "./workers.js";

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
  narrate(heard: string, answer: string, expectedProcessId: string, signal: AbortSignal): Promise<string>;
}

type IdleHooks = { chunk(text: string): void; finish(): void; recover(): void };
type Identity = { sessionId: string; utteranceId: string };
type Utterance = { snapshot?: Promise<VoiceSnapshot>; submitted: boolean; decisions: Map<string, VoiceDecision> };
type Reply = { id: string; utteranceId: string; cancel: AbortController; text: string;
  spoken: number; output: Promise<void>; heard: string; processId: string };
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
  recognizer: Recognizer;
  synthesizer: Synthesizer;
  utterances: Map<string, Utterance>;
  receipts: number;
  reply: Reply | undefined;
};

type Options = {
  profile: string;
  helpers: string;
  platform: string;
  architecture: string;
  thread(id: string): Promise<VoiceThread>;
  publish(event: Record<string, unknown>): void;
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
      case "start": return this.start(request.threadId, request.stt, request.tts);
      case "stop": {
        if (this.session?.id === request.sessionId) await this.end();
        return {};
      }
    }
    const session = this.requireSession(request.sessionId);
    switch (request.type) {
      case "interrupt": this.interrupt(session); return {};
      case "snapshot": this.snapshot(session, request.utteranceId); return {};
      case "decode": return this.decode(session, request, request.pcm, request.final);
      case "submit": return this.submit(session, request.utteranceId, request.text, request.intent);
      case "decide": return this.decide(session, request.utteranceId, request.text, request.silenceMs, request.finalCall);
      case "commit": return this.commit(session, request.utteranceId, request.decisionId);
    }
  }

  async end(reason?: string): Promise<void> {
    this.startup?.abort();
    const session = this.session;
    if (!session) return this.stopping;
    this.session = undefined;
    this.interrupt(session);
    session.signal.abort();
    const stopped = Promise.all([session.recognizer.close(), session.synthesizer.close()]).then(() => {});
    this.stopping = stopped;
    this.options.publish({ type: "ended", sessionId: session.id, ...(reason ? { reason } : {}) });
    try { await stopped; }
    finally { if (this.stopping === stopped) this.stopping = undefined; }
  }

  async dispose(): Promise<void> {
    await Promise.all([this.end(), this.assets.cancelAndWait()]);
  }

  private async helperExists(name: string): Promise<boolean> {
    try {
      const path = join(this.options.helpers, name);
      if (!(await lstat(path)).isFile()) return false;
      await access(path, constants.X_OK);
      return true;
    } catch { return false; }
  }

  private async start(threadId: string, model: VoiceModel, tts: "system" | "pocket") {
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
      recognizer = new Recognizer(join(this.options.helpers, "crucible-whisper"), modelPath, failed);
      synthesizer = new Synthesizer(join(this.options.helpers, tts === "system" ? "crucible-tts" : "crucible-pocket-tts"),
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
      const sessionId = randomUUID();
      this.session = { id: sessionId, thread, signal: abort, model, tts, recognizer, synthesizer, utterances: new Map(), receipts: 0, reply: undefined };
      return { sessionId };
    } catch (error) {
      await Promise.all([recognizer?.close(), synthesizer?.close()]);
      throw error;
    } finally { this.startup = undefined; }
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
    try {
      decision = await session.thread.decide({ utterance: text, silenceMs, finalCall, expectedProcessId: snapshot.processId },
        AbortSignal.any([session.signal.signal, AbortSignal.timeout(15_000)]));
    } catch (error) {
      const refused = refusalOf(error);
      if (refused) {
        const message = refusalMessage(error);
        return message ? { refused, message } : { refused };
      }
      throw error;
    }
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
        this.deliverReceipt(session, result.receipt, decisionId, turn);
        this.append(session, reply, decision.say, true);
        this.speak(session, reply, decision.say);
        if (result.receipt.status === "queued") void this.watchReceipt(session, decisionId, turn);
        break;
      }
      case "start_task":
        // The acknowledgement is spoken only; the panel shows the agent's own answer.
        this.speak(session, reply, decision.say);
        this.startIdle(session, reply, result.message, result.voiceRequest);
        break;
    }
    return { action: result.action };
  }

  private startIdle(session: Session, reply: Reply, text: string, voiceRequest: string | undefined): void {
    const failed = () => this.error(session, "The spoken message could not be sent. Review its transcript and try again.");
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
    void session.thread.narrate(reply.heard, answer, reply.processId,
      AbortSignal.any([reply.cancel.signal, AbortSignal.timeout(20_000)]))
      .catch(() => `${firstSentences(speakable(answer), 2)} The rest is on screen.`)
      .then(say => { if (this.currentReply(session, reply)) this.speak(session, reply, say); });
  }

  private deliverReceipt(session: Session, receipt: ConversationReceipt, id: string, turn: string): void {
    if (receipt.message_id !== id || receipt.turn_id !== turn) throw new Error("Received a voice receipt for a different coding turn.");
    this.options.publish({ type: "receipt", sessionId: session.id, receipt });
  }

  private async watchReceipt(session: Session, id: string, turn: string): Promise<void> {
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
          this.deliverReceipt(session, receipt, id, turn);
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
      spoken: 0, output: Promise.resolve(), heard, processId };
    session.reply = reply;
    return reply;
  }

  private interrupt(session: Session): void {
    session.reply?.cancel.abort();
    session.reply = undefined;
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
    reply.output = reply.output.then(async () => {
      if (!this.currentReply(session, reply)) return;
      await session.synthesizer.speak(text, (pcm, sampleRate) => {
        if (this.currentReply(session, reply)) this.options.publish({ type: "audio", sessionId: session.id,
          utteranceId: reply.utteranceId, replyId: reply.id, pcm, sampleRate });
      }, reply.cancel.signal);
    }).catch(() => {
      if (this.currentReply(session, reply)) this.error(session, "The reply is available in text. The local voice could not read it.");
    });
  }

  private currentReply(session: Session, reply: Reply): boolean {
    return this.session === session && session.reply === reply && !reply.cancel.signal.aborted && session.thread.current();
  }

  private error(session: Session, message: string): void {
    if (this.session === session && session.thread.current()) this.options.publish({ type: "error", sessionId: session.id, message });
  }
}
