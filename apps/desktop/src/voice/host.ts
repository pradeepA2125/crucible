import { randomUUID } from "node:crypto";
import { access, lstat } from "node:fs/promises";
import { constants } from "node:fs";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import type { ConversationIntent, ConversationReceipt, ConversationRequest } from "@crucible/editor-client";
import { VoiceAssets, type VoiceModel } from "./assets.js";
import type { VoiceRequest } from "./protocol.js";
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
  sendIdle(text: string, hooks: { chunk(text: string): void; finish(): void; recover(): void }): Promise<void>;
}

type Identity = { sessionId: string; utteranceId: string };
type Utterance = { snapshot?: Promise<VoiceSnapshot>; submitted: boolean };
type Reply = { id: string; utteranceId: string; cancel: AbortController; text: string;
  pending: string; spoken: number; code: boolean; output: Promise<void> };
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
    session.utterances.set(id, { snapshot, submitted: false });
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
    const reply = this.newReply(session, id);
    const snapshot = await entry.snapshot;
    this.requireSession(session.id);
    if (!snapshot.active) {
      if (intent === "steer") throw new Error("There was no active coding turn when this message began.");
      const current = await session.thread.snapshot(AbortSignal.any([session.signal.signal, AbortSignal.timeout(4000)]));
      this.requireSession(session.id);
      if (current.active || !snapshot.processId || snapshot.processId !== current.processId) throw new Error(changedTurn);
      void session.thread.sendIdle(text, {
        chunk: text => this.append(session, reply, text, false),
        finish: () => this.append(session, reply, "", true),
        recover: () => this.error(session, "The spoken message could not be sent. Review its transcript and try again."),
      }).catch(() => this.error(session, "The spoken message could not be sent. Review its transcript and try again."));
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
    if (receipt.status === "queued") void this.watchReceipt(session, id, snapshot.turnId);
    return {};
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

  private newReply(session: Session, id: string): Reply {
    this.interrupt(session);
    const reply = { id: randomUUID(), utteranceId: id, cancel: new AbortController(), text: "",
      pending: "", spoken: 0, code: false, output: Promise.resolve() };
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
    reply.pending += next;
    while (reply.pending && reply.spoken < limitSpeech) {
      const boundary = /[.!?](?:\s|$)|\n/.exec(reply.pending);
      const codeFence = reply.pending.indexOf("```");
      const end = codeFence >= 0 && (!boundary || codeFence <= boundary.index)
        ? codeFence + 3
        : boundary ? boundary.index + boundary[0].length
          : reply.pending.length >= 220 ? Math.max(reply.pending.lastIndexOf(" ", 180), 180)
            : final ? reply.pending.length : 0;
      if (!end) break;
      const segment = reply.pending.slice(0, end);
      reply.pending = reply.pending.slice(end);
      if (!reply.code) this.speak(session, reply, segment.split("```")[0] ?? "");
      if (segment.endsWith("```")) reply.code = !reply.code;
    }
  }

  private speak(session: Session, reply: Reply, segment: string): void {
    const text = segment.replace(/!?\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/https?:\/\/\S+/g, "")
      .replace(/[`*_#]/g, "").trim().slice(0, limitSpeech - reply.spoken);
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
