import { describe, expect, it, vi } from "vitest";
import type { VoiceBridge, VoiceCommand, VoiceEvent } from "./voiceBridge";
import { VoiceSession } from "./voiceSession";
import { CAPTURE_FRAME_BYTES, type CaptureFrame } from "./speechSegments";

const audio = vi.hoisted(() => ({
  playing: false,
  play: vi.fn(), stopPlayback: vi.fn<() => number | undefined>(), close: vi.fn(), closeMicrophone: vi.fn(),
  setMicrophoneEnabled: vi.fn(), cue: vi.fn(() => 400),
  pausePlayback: vi.fn(), resumePlayback: vi.fn(), paused: false, outputLevel: vi.fn(() => 0),
  openMicrophone: vi.fn(async (_options: { onFrame(frame: CaptureFrame): void }) => true), resume: vi.fn(async () => {}),
}));

vi.mock("./voiceAudio", () => ({
  VoiceAudio: class {
    get playing() { return audio.playing; }
    play = audio.play;
    stopPlayback = audio.stopPlayback;
    close = audio.close;
    closeMicrophone = audio.closeMicrophone;
    setMicrophoneEnabled = audio.setMicrophoneEnabled;
    cue = audio.cue;
    pausePlayback = audio.pausePlayback;
    resumePlayback = audio.resumePlayback;
    get paused() { return audio.paused; }
    outputLevel = audio.outputLevel;
    openMicrophone = audio.openMicrophone;
    resume = audio.resume;
  },
}));

type Decision = { turn: string; action: string; say: string; request: string };
const REPLY: Decision = { turn: "complete", action: "reply", say: "Sure.", request: "" };

function fixture(transcript = "Use SQLite", decision: Decision = REPLY) {
  let subscriber: ((event: VoiceEvent) => void) | undefined;
  const unsubscribe = vi.fn(() => { subscriber = undefined; });
  let decisions = 0;
  const current = { decision };
  const request = vi.fn(async (command: VoiceCommand): Promise<unknown> => {
    switch (command.type) {
      case "start": return { sessionId: "session" };
      case "decode": return { text: transcript };
      case "decide": return { ...current.decision, decisionId: `d${++decisions}` };
      case "commit": return { action: current.decision.action };
      default: return {};
    }
  });
  const bridge: VoiceBridge = { request, subscribe: callback => { subscriber = callback; return unsubscribe; } };
  const callbacks = {
    state: vi.fn(), utterance: vi.fn(), reply: vi.fn(), receipt: vi.fn(), error: vi.fn(), ended: vi.fn(),
  };
  const trace = vi.fn<(sessionId: string, event: string, data: Record<string, unknown>) => void>();
  return {
    request, unsubscribe, callbacks, trace, session: new VoiceSession(bridge, callbacks, trace),
    emit(event: VoiceEvent) { subscriber?.(event); },
    decide(next: Decision) { current.decision = next; },
    sent: (type: VoiceCommand["type"]) => request.mock.calls.map(([command]) => command).filter(command => command.type === type),
  };
}

const settle = () => new Promise(resolve => setTimeout(resolve, 0));

function frames(count: number, rms: number) {
  const { onFrame } = audio.openMicrophone.mock.calls[0][0];
  for (let index = 0; index < count; index += 1) {
    onFrame({ pcm: new ArrayBuffer(CAPTURE_FRAME_BYTES), rms });
  }
}

describe("desktop voice session", () => {
  it("uses one snapshot per typed message and rejects PCM from a superseded utterance", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    await voice.session.start("thread", "whisper-small", "system");
    await voice.session.submitText("Status?", "question");
    const first = voice.request.mock.calls.map(([command]) => command).find(command => command.type === "submit");
    expect(first).toMatchObject({ type: "submit", intent: "question" });
    if (first?.type !== "submit") throw new Error("Expected submitted message");
    expect(voice.request).toHaveBeenCalledWith({
      type: "snapshot", sessionId: "session", utteranceId: first.utteranceId,
    });
    const pcm = new ArrayBuffer(2);
    const previousAudio = {
      type: "audio", sessionId: "session", utteranceId: first.utteranceId, replyId: "first", pcm, sampleRate: 24_000,
    } as const;
    voice.emit(previousAudio);
    expect(audio.play).toHaveBeenCalledOnce();

    const later = voice.session.submitText("Use SQLite", "steer");
    expect(audio.stopPlayback).toHaveBeenCalledTimes(2);
    voice.emit(previousAudio);
    await later;
    voice.emit(previousAudio);
    expect(audio.play).toHaveBeenCalledOnce();

    voice.session.close();
    expect(voice.unsubscribe).toHaveBeenCalledOnce();
    expect(audio.close).toHaveBeenCalledOnce();
    expect(voice.request).toHaveBeenLastCalledWith({ type: "stop", sessionId: "session" });
  });

  it("submits finalized microphone audio once and ignores callbacks from a muted microphone", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    const { onFrame } = audio.openMicrophone.mock.calls[0][0];
    const feed = (count: number, rms: number) => {
      for (let index = 0; index < count; index += 1) {
        onFrame({ pcm: new ArrayBuffer(CAPTURE_FRAME_BYTES), rms });
      }
    };

    feed(8, 0.03);
    const snapshot = voice.request.mock.calls.map(([command]) => command).find(command => command.type === "snapshot");
    if (snapshot?.type !== "snapshot") throw new Error("Expected a snapshot at speech onset");
    feed(125, 0);
    await vi.waitFor(() => expect(voice.request).toHaveBeenCalledWith({
      type: "commit", sessionId: "session", utteranceId: snapshot.utteranceId, decisionId: "d1",
    }));
    expect(voice.sent("decide")).toEqual([{ type: "decide", sessionId: "session", utteranceId: snapshot.utteranceId,
      text: "Use SQLite", silenceMs: expect.any(Number), finalCall: false }]);
    expect(voice.callbacks.utterance).toHaveBeenCalledExactlyOnceWith(snapshot.utteranceId, "Use SQLite");

    voice.session.mute();
    feed(8, 0.03);
    feed(28, 0);
    expect(voice.request.mock.calls.filter(([command]) => command.type === "snapshot")).toHaveLength(1);
    expect(voice.sent("commit")).toHaveLength(1);
    expect(voice.sent("submit")).toHaveLength(0);
    voice.session.close();
  });

  it("only sends push-to-talk audio on release, even across a long pause", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    voice.session.setPushToTalk(true);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.setMicrophoneEnabled).toHaveBeenLastCalledWith(false));
    const { onFrame } = audio.openMicrophone.mock.calls[0][0];
    const feed = (count: number, rms: number) => {
      for (let index = 0; index < count; index += 1) {
        onFrame({ pcm: new ArrayBuffer(CAPTURE_FRAME_BYTES), rms });
      }
    };
    const commands = () => voice.request.mock.calls.map(([command]) => command.type);

    feed(10, 0.03);
    expect(commands()).not.toContain("snapshot");
    voice.session.beginHold();
    expect(audio.setMicrophoneEnabled).toHaveBeenLastCalledWith(true);
    feed(10, 0.03);
    feed(65, 0);
    expect(commands()).toContain("snapshot");
    expect(commands()).not.toContain("decode");
    voice.session.sendNow();
    expect(audio.setMicrophoneEnabled).toHaveBeenLastCalledWith(false);
    await vi.waitFor(() => expect(commands()).toContain("commit"));
    expect(voice.sent("decide")).toEqual([expect.objectContaining({ finalCall: true })]);
    feed(10, 0.03);
    expect(commands().filter(command => command === "snapshot")).toHaveLength(1);
    voice.session.beginHold();
    feed(10, 0.03);
    voice.session.cancelHold();
    voice.session.sendNow();
    await settle();
    expect(commands().filter(command => command === "commit")).toHaveLength(1);
    voice.session.close();
  });

  it("records playback stop without sending a message", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    await voice.session.start("thread", "whisper-small", "system");
    audio.stopPlayback.mockImplementationOnce(() => performance.now() + 15);
    voice.session.stopSpeaking();
    expect(voice.callbacks.state).toHaveBeenCalledWith({ playbackStopMs: expect.any(Number) });
    expect(voice.request).toHaveBeenLastCalledWith({ type: "interrupt", sessionId: "session" });
    expect(voice.request.mock.calls.some(([command]) => command.type === "submit")).toBe(false);
    voice.session.close();
  });

  it("discards a pending transcription when the speaker resumes", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite instead.");
    const defaultRequest = voice.request.getMockImplementation()!;
    let resolveFirst!: (value: { text: string }) => void;
    voice.request.mockImplementation(command => {
      if (command.type === "decode" && !resolveFirst) {
        return new Promise(resolve => { resolveFirst = resolve; });
      }
      return defaultRequest(command);
    });
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(125, 0);
    await vi.waitFor(() => expect(resolveFirst).toBeDefined());
    frames(15, 0.03);
    resolveFirst({ text: "Use Postgres." });
    await vi.waitFor(() => expect(voice.trace.mock.calls.some(([, event]) => event === "words_resumed")).toBe(true));
    frames(125, 0);
    await vi.waitFor(() => expect(voice.callbacks.utterance)
      .toHaveBeenCalledExactlyOnceWith(expect.any(String), "Use SQLite instead."));
    expect(voice.request.mock.calls.filter(([command]) => command.type === "snapshot")).toHaveLength(1);
    voice.session.close();
  });

  it("returns to listening when transcription fails without a transcript to review", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    const request = voice.request.getMockImplementation()!;
    voice.request.mockImplementation(command => command.type === "decode"
      ? Promise.reject(new Error("Transcription timed out"))
      : request(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(400, 0);
    await vi.waitFor(() => expect(voice.callbacks.state)
      .toHaveBeenLastCalledWith({ capturing: false, checking: false, waiting: false, deciding: false, slowDecision: false, partial: "" }));
    expect(voice.request).not.toHaveBeenCalledWith(expect.objectContaining({ type: "clarify" }));
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();

    voice.request.mockImplementation(request);
    frames(12, 0.03);
    frames(125, 0);
    await vi.waitFor(() => expect(voice.callbacks.utterance)
      .toHaveBeenCalledExactlyOnceWith(expect.any(String), "Use SQLite"));
    voice.session.close();
  });

  it.each([
    { transcript: "(mouse clicks) [door slamming]", playback: undefined, submitted: false },
    { transcript: "", playback: "I have an update for you.", submitted: false, keepsSpeaking: true },
    { transcript: "(cough) [door slamming]", playback: "I have an update for you.", submitted: false, keepsSpeaking: true },
    { transcript: "Um, hmm.", playback: "I have an update for you.", submitted: false, keepsSpeaking: true },
    { transcript: "Mm-hmm.", playback: "I have an update for you.", submitted: false, keepsSpeaking: true },
    { transcript: "I'll apply that at my next step.", playback: "Got it. I'll apply that at my next step.", submitted: false, keepsSpeaking: true },
    { transcript: "Current status.", playback: "Here is the current status.", submitted: false, keepsSpeaking: true },
    { transcript: "Stop speaking.", playback: "You can say stop speaking at any point.", submitted: false, keepsSpeaking: true },
    { transcript: "Stop speaking, please.", playback: "I have an update for you.", submitted: false },
    { transcript: "Please stop speaking.", playback: undefined, submitted: false },
    { transcript: "Crucible, could you stop speaking, please?", playback: undefined, submitted: false },
    { transcript: "- Yeah.", playback: undefined, submitted: false },
    { transcript: "Stop coding.", playback: undefined, submitted: true },
    { transcript: "Stop coding and show the diff.", playback: "I have an update for you.", submitted: true, cleanTail: true },
    { transcript: "(cough) Use SQLite.", playback: "I have an update for you.", submitted: true, cleanTail: true },
    { transcript: "start speaking to you", playback: "I have an update for you.", submitted: false, held: true },
    { transcript: "proofs will stop talking", playback: "I have an update for you.", submitted: false, held: true },
    { transcript: "of talking", playback: "I have an update for you.", submitted: false, held: true },
    { transcript: "Use SQLite.", playback: "I have an update for you.", submitted: false, held: true },
    { transcript: "SQLite.", playback: "I have an update for you.", submitted: false, held: true, keepsSpeaking: true },
    { transcript: "Was I speaking to you?", playback: "I have an update for you.", submitted: false, held: true, cleanTail: true },
    { transcript: "Use SQLite for this test.", playback: "I have an update for you.", submitted: true, cleanTail: true },
  ])("filters non-message audio without losing corrections: $transcript", async ({ transcript, playback, submitted, held, cleanTail, keepsSpeaking }) => {
    vi.clearAllMocks();
    const voice = fixture(transcript);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());

    if (playback) {
      await voice.session.submitText("Status?", "question");
      const initial = voice.request.mock.calls.map(([command]) => command).find(command => command.type === "submit");
      if (initial?.type !== "submit") throw new Error("Expected initial voice reply target");
      voice.emit({ type: "reply", sessionId: "session", messageId: "reply", utteranceId: initial.utteranceId, text: playback, final: true });
    }

    audio.playing = playback !== undefined;
    if (playback) {
      audio.stopPlayback.mockImplementationOnce(() => {
        audio.playing = false;
        return performance.now();
      });
    }
    try {
      frames(keepsSpeaking || cleanTail ? 16 : 8, 0.03);
      if (playback) expect(audio.stopPlayback).toHaveBeenCalledOnce();
      if (cleanTail) {
        await vi.waitFor(() => expect(audio.stopPlayback).toHaveBeenCalledTimes(2));
        frames(28, 0.03);
      }
      frames(submitted ? 125 : 28, 0);
      if (submitted) await vi.waitFor(() => expect(voice.callbacks.utterance).toHaveBeenCalledWith(expect.any(String), transcript));
      else if (held) {
        await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ partial: transcript }));
        await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenLastCalledWith({ checking: false }));
        frames(372, 0);
        await vi.waitFor(() => expect(voice.callbacks.state)
          .toHaveBeenCalledWith({ reviews: [{ id: expect.any(String), transcript }] }));
        expect(voice.sent("decide")).toHaveLength(0);
      } else {
        await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ capturing: false, checking: false, waiting: false, deciding: false, slowDecision: false, partial: "" }));
      }
      await settle();
      expect(voice.sent("commit")).toHaveLength(submitted ? 1 : 0);
      if (submitted) expect(voice.sent("decide")[0]).toMatchObject({ text: transcript });
      if (playback) {
        expect(audio.stopPlayback).toHaveBeenCalledTimes(keepsSpeaking ? 1 : 2);
        expect(audio.playing).toBe(keepsSpeaking === true);
        expect(voice.request.mock.calls.filter(([command]) => command.type === "interrupt"))
          .toHaveLength(keepsSpeaking ? 1 : 2);
      }
    } finally {
      audio.playing = false;
      voice.session.close();
    }
  });

  it("keeps an interrupted draft when final decoding returns nothing, including after new input and mute", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    const request = voice.request.getMockImplementation()!;
    let final = "";
    voice.request.mockImplementation(command => command.type === "decode"
      ? Promise.resolve({ text: command.final ? final : "Use SQLite instead." }) : request(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    await voice.session.submitText("Status?", "question");
    audio.playing = true;
    audio.stopPlayback.mockImplementationOnce(() => {
      audio.playing = false;
      return performance.now();
    });
    try {
      frames(16, 0.03);
      frames(28, 0);
      await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({
        reviews: [{ id: expect.any(String), transcript: "Use SQLite instead." }],
      }));
      const draft = voice.callbacks.state.mock.calls.find(([patch]) => patch.reviews?.length)?.[0].reviews[0];
      expect(draft).toBeDefined();
      final = "Switch to the";
      const answer = voice.request.getMockImplementation()!;
      voice.request.mockImplementation(command => command.type === "decide"
        ? Promise.resolve({ refused: "voice_agent_unavailable" }) : answer(command));
      frames(8, 0.03);
      frames(400, 0);
      await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenLastCalledWith({ reviews: [
        draft, { id: expect.any(String), transcript: final },
      ] }));
      voice.session.mute();
      expect(voice.callbacks.utterance).toHaveBeenCalledOnce();
      voice.session.sendReviewed(draft.id);
      await vi.waitFor(() => expect(voice.callbacks.utterance)
        .toHaveBeenLastCalledWith(draft.id, "Use SQLite instead."));
      await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({
        reviews: [{ id: expect.any(String), transcript: final }],
      }));
    } finally {
      audio.playing = false;
      voice.session.close();
    }
  });

  it("keeps the transcript available to edit when the original turn rejects it", async () => {
    vi.clearAllMocks();
    const voice = fixture("Switch to the");
    const request = voice.request.getMockImplementation()!;
    voice.request.mockImplementation(command => command.type === "submit"
      ? Promise.reject(new Error("The coding turn changed."))
      : command.type === "decide" ? Promise.resolve({ refused: "voice_agent_unavailable" }) : request(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(400, 0);
    await vi.waitFor(() => expect(voice.callbacks.state)
      .toHaveBeenLastCalledWith({ reviews: [{ id: expect.any(String), transcript: "Switch to the" }] }));
    const [{ id }] = voice.callbacks.state.mock.lastCall?.[0].reviews;
    voice.session.sendReviewed(id);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({
      reviews: [{ id, transcript: "Switch to the", status: "failed" }],
    }));
    expect(voice.callbacks.error).toHaveBeenCalledWith("The coding turn changed.");
    voice.session.sendReviewed(id);
    expect(voice.request.mock.calls.filter(([command]) => command.type === "submit")).toHaveLength(1);
    voice.session.close();
  });

  it("stops on a quiet spoken command before the send check and drops late audio", async () => {
    vi.clearAllMocks();
    const voice = fixture("Stop.");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    await voice.session.submitText("Status?", "question");
    const initial = voice.request.mock.calls.map(([command]) => command).find(command => command.type === "submit");
    if (initial?.type !== "submit") throw new Error("Expected initial voice reply target");
    const chunk = { type: "audio", sessionId: "session", utteranceId: initial.utteranceId, replyId: "reply",
      pcm: new ArrayBuffer(2), sampleRate: 24_000 } as const;
    voice.emit(chunk);
    audio.playing = true;
    audio.stopPlayback.mockImplementationOnce(() => {
      audio.playing = false;
      return performance.now();
    });
    try {
      frames(10, 0.012);
      frames(7, 0);
      await vi.waitFor(() => expect(audio.stopPlayback).toHaveBeenCalledTimes(2));
      expect(voice.request.mock.calls.filter(([command]) => command.type === "decode"))
        .toEqual([[expect.objectContaining({ final: false })]]);
      voice.emit(chunk);
      expect(audio.play).toHaveBeenCalledOnce();
      frames(218, 0);
      await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenLastCalledWith({ capturing: false, checking: false, waiting: false, deciding: false, slowDecision: false, partial: "" }));
      expect(voice.request.mock.calls.filter(([command]) => command.type === "submit")).toHaveLength(1);
    } finally {
      audio.playing = false;
      voice.session.close();
    }
  });

  it("decides at the pause and listens on an unfinished turn", async () => {
    vi.clearAllMocks();
    const voice = fixture("I want you to", { turn: "cut_off", action: "listen", say: "", request: "" });
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ waiting: true }));
    frames(150, 0);
    await settle();
    expect(voice.sent("decide")).toEqual([expect.objectContaining({ text: "I want you to", finalCall: false })]);
    expect(voice.sent("commit")).toHaveLength(0);
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();
    voice.session.close();
  });

  it("asks again with finalCall at the wait limit", async () => {
    vi.clearAllMocks();
    const voice = fixture("so the narration", { turn: "thinking", action: "listen", say: "", request: "" });
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    await settle();
    voice.decide({ turn: "complete", action: "clarify", say: "Go on?", request: "" });
    frames(371, 0);
    await settle();
    expect(voice.sent("decide")).toHaveLength(1);
    frames(1, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    expect(voice.sent("decide")[1]).toMatchObject({ finalCall: true, silenceMs: 8000 });
    voice.session.close();
  });

  it("resumed speech drops an in-flight decision", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    const answer = voice.request.getMockImplementation()!;
    let decodes = 0, release!: (value: unknown) => void;
    voice.request.mockImplementation(command => {
      if (command.type === "decode") return Promise.resolve({ text: ++decodes === 1 ? "Review the" : "Review the voice feature" });
      if (command.type === "decide" && !release) return new Promise(resolve => { release = resolve; });
      return answer(command);
    });
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(release).toBeDefined());
    frames(10, 0.03);
    release({ ...REPLY, decisionId: "stale" });
    frames(1, 0);
    await vi.waitFor(() => expect(voice.trace.mock.calls.some(([, event]) => event === "words_resumed")).toBe(true));
    frames(40, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    expect(voice.sent("commit")[0]).not.toMatchObject({ decisionId: "stale" });
    expect(voice.sent("decide")[1]).toMatchObject({ text: "Review the voice feature" });
    voice.session.close();
  });

  it("commits a complete reply only after the floor", async () => {
    vi.clearAllMocks();
    const voice = fixture("How is it going?");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    await settle();
    frames(6, 0);
    await settle();
    expect(voice.sent("commit")).toHaveLength(0);
    frames(1, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    voice.session.close();
  });

  it("holds a task for two seconds and merges a continuation", async () => {
    vi.clearAllMocks();
    const voice = fixture("", { turn: "complete", action: "start_task", say: "On it.", request: "x" });
    const answer = voice.request.getMockImplementation()!;
    let decodes = 0;
    voice.request.mockImplementation(command => command.type === "decode" && command.final
      ? Promise.resolve({ text: ++decodes === 1 ? "Add a readme" : "Add a readme with a license section" })
      : command.type === "decode" ? Promise.resolve({ text: decodes ? "Add a readme with a license section" : "" })
        : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    await settle();
    frames(80, 0);
    await settle();
    expect(voice.sent("commit")).toHaveLength(0);
    frames(10, 0.03);
    frames(1, 0);
    await vi.waitFor(() => expect(voice.trace.mock.calls.some(([, event]) => event === "words_resumed")).toBe(true));
    frames(28, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(2));
    expect(voice.sent("decide")[1]).toMatchObject({ text: "Add a readme with a license section" });
    expect(voice.sent("commit")).toHaveLength(0);
    voice.session.close();
  });

  it("start_task commits after the hold", async () => {
    vi.clearAllMocks();
    const voice = fixture("Add a readme", { turn: "complete", action: "start_task", say: "On it.", request: "Add a README" });
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    await settle();
    frames(106, 0);
    await settle();
    expect(voice.sent("commit")).toHaveLength(0);
    frames(1, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    voice.session.close();
  });

  it("turn_active on commit re-decides once with finalCall", async () => {
    vi.clearAllMocks();
    const voice = fixture("Add a readme", { turn: "complete", action: "start_task", say: "On it.", request: "Add a README" });
    const answer = voice.request.getMockImplementation()!;
    let commits = 0;
    voice.request.mockImplementation(command => command.type === "commit" && ++commits === 1
      ? Promise.resolve({ refused: "turn_active" }) : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(150, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(2));
    expect(voice.sent("decide")).toEqual([
      expect.objectContaining({ finalCall: false }), expect.objectContaining({ finalCall: true, text: "Add a readme" })]);
    expect(voice.sent("commit")[1]).toMatchObject({ decisionId: "d2" });
    voice.session.close();
  });

  it.each(["mute", "close"] as const)("%s during the hold sends no commit", async action => {
    vi.clearAllMocks();
    const voice = fixture("Add a readme", { turn: "complete", action: "start_task", say: "On it.", request: "Add a README" });
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    await settle();
    frames(50, 0);
    if (action === "mute") voice.session.mute(); else voice.session.close();
    frames(200, 0);
    await settle();
    expect(voice.sent("commit")).toHaveLength(0);
    if (action === "mute") voice.session.close();
  });

  it("a backchannel at a pause never decides", async () => {
    vi.clearAllMocks();
    const voice = fixture("Mm-hmm.");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(150, 0);
    await settle();
    expect(voice.sent("decide")).toHaveLength(0);
    expect(voice.sent("commit")).toHaveLength(0);
    voice.session.close();
  });

  it("rollover keeps one request across 30 seconds", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    const answer = voice.request.getMockImplementation()!;
    let finals = 0, partials = 0;
    voice.request.mockImplementation(command => command.type === "decode" && command.final
      ? Promise.resolve({ text: ++finals === 1 ? "First I want the timer gone" : "and then the narration fixed." })
      // Live transcripts grow as the speaker talks.
      : command.type === "decode" ? Promise.resolve({ text: Array.from({ length: ++partials }, () => "word").join(" ") })
        : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    for (let sent = 0; sent < 1475; sent += 25) { frames(Math.min(25, 1475 - sent), 0.03); await settle(); }
    frames(40, 0.03);
    frames(60, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    expect(voice.callbacks.error).not.toHaveBeenCalled();
    expect(voice.sent("decide")).toEqual([expect.objectContaining({
      text: "First I want the timer gone and then the narration fixed." })]);
    expect(voice.request.mock.calls.filter(([command]) => command.type === "snapshot")).toHaveLength(1);
    voice.session.close();
  });

  it("traces levels, the pause, the decision and the words that resumed the turn", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    await settle();
    const answer = voice.request.getMockImplementation()!;
    voice.request.mockImplementation(command => command.type === "decode"
      ? Promise.resolve({ text: "Use SQLite for the tests and the docs" }) : answer(command));
    frames(4, 0.03);
    frames(10, 0);
    await vi.waitFor(() => expect(voice.trace.mock.calls.some(([, event]) => event === "resume")).toBe(true));
    const events = voice.trace.mock.calls.map(([sessionId, event, data]) => ({ sessionId, event, data }));
    const named = (name: string) => events.filter(entry => entry.event === name);
    expect(events.every(entry => entry.sessionId === "session")).toBe(true);
    expect(named("onset")).toHaveLength(1);
    expect(named("pause")[0].data).toMatchObject({ silenceMs: 560 });
    expect(named("transcript")[0].data).toMatchObject({ final: true, text: "Use SQLite for the tests" });
    expect(named("decide")[0].data).toMatchObject({ text: "Use SQLite for the tests", finalCall: false });
    expect(named("decision")[0].data).toMatchObject({ action: "reply", turn: "complete", ms: expect.any(Number) });
    expect(named("words_resumed")[0].data).toMatchObject({ after: "Use SQLite for the tests and the docs" });
    expect(named("resume")[0].data).toMatchObject({ quietMs: expect.any(Number), droppedDecision: true });
    expect(named("levels")[0].data).toMatchObject({
      peak: expect.any(Number), mean: expect.any(Number), noise: expect.any(Number), loudFrames: expect.any(Number),
      continueAt: expect.any(Number), silenceMs: expect.any(Number), inTurn: true });
    voice.session.close();
  });

  /** Answers vad requests like the native detector: one probability per 512 samples sent. */
  function withDetector(voice: ReturnType<typeof fixture>, probability: () => number | null) {
    const answer = voice.request.getMockImplementation()!;
    let sent = 0;
    voice.request.mockImplementation(command => {
      if (command.type === "start") return Promise.resolve({ sessionId: "session", vad: true });
      if (command.type !== "vad") return answer(command);
      const level = probability();
      if (level === null) return Promise.resolve({ probs: null });
      const before = Math.floor(sent / 512);
      sent += command.pcm.byteLength / 2;
      return Promise.resolve({ probs: Array.from({ length: Math.floor(sent / 512) - before }, () => level) });
    });
  }

  /** A reply playing at level 0.1, with the speech detector running at `level()`. */
  async function replying(transcript: string, level: { value: number }) {
    vi.clearAllMocks();
    const voice = fixture(transcript);
    withDetector(voice, () => level.value);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    await voice.session.submitText("Status?", "question");
    const initial = voice.request.mock.calls.map(([command]) => command).find(command => command.type === "submit");
    if (initial?.type !== "submit") throw new Error("Expected initial voice reply target");
    voice.emit({ type: "reply", sessionId: "session", messageId: "reply", utteranceId: initial.utteranceId,
      text: "Here is the status of the work.", final: true });
    audio.playing = true;
    audio.outputLevel.mockReturnValue(0.1);
    const interruptsBefore = voice.sent("interrupt").length;
    audio.stopPlayback.mockClear(); // the setup's own typed message stopped nothing that matters
    const feed = async (count: number, rms: number) => {
      for (let sent = 0; sent < count; sent += 4) { frames(Math.min(4, count - sent), rms); await settle(); }
    };
    return { voice, feed, interruptsBefore };
  }

  it("a cough over a reply pauses it and resumes from the same point", async () => {
    const level = { value: 0.9 };
    const { voice, feed } = await replying("(cough)", level);
    try {
      await feed(8, 0.4);
      expect(audio.pausePlayback).toHaveBeenCalledOnce();
      level.value = 0.05;
      await feed(80, 0);
      await vi.waitFor(() => expect(audio.resumePlayback).toHaveBeenCalledOnce());
      expect(audio.stopPlayback).not.toHaveBeenCalled();
      expect(voice.sent("decide")).toHaveLength(0);
    } finally { audio.playing = false; audio.outputLevel.mockReturnValue(0); voice.session.close(); }
  });

  it("the reply's own echo never pauses it", async () => {
    const level = { value: 0.95 };
    const { voice, feed } = await replying("Here is the status of the work.", level);
    try {
      await feed(300, 0.03);
      expect(audio.pausePlayback).not.toHaveBeenCalled();
      expect(voice.trace.mock.calls.some(([, event]) => event === "onset")).toBe(false);
    } finally { audio.playing = false; audio.outputLevel.mockReturnValue(0); voice.session.close(); }
  });

  it("real words over a reply stop it for good and are decided", async () => {
    const level = { value: 0.9 };
    const { voice, feed, interruptsBefore } = await replying("No, I meant the other file", level);
    try {
      await feed(24, 0.4);
      level.value = 0.05;
      await feed(40, 0);
      await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
      expect(voice.sent("decide")[0]).toMatchObject({ text: "No, I meant the other file" });
      expect(voice.sent("interrupt").length).toBeGreaterThan(interruptsBefore);
      expect(audio.resumePlayback).not.toHaveBeenCalled();
    } finally { audio.playing = false; audio.outputLevel.mockReturnValue(0); voice.session.close(); }
  });

  it("\"wait\" over a reply stops it for good", async () => {
    const level = { value: 0.9 };
    const { voice, feed, interruptsBefore } = await replying("Wait.", level);
    try {
      await feed(24, 0.4);
      level.value = 0.05;
      await feed(40, 0);
      await vi.waitFor(() => expect(voice.sent("interrupt").length).toBeGreaterThan(interruptsBefore));
      expect(audio.resumePlayback).not.toHaveBeenCalled();
    } finally { audio.playing = false; audio.outputLevel.mockReturnValue(0); voice.session.close(); }
  });

  it("a turn that starts over the reply is decided once it has a second of clear speech", async () => {
    vi.clearAllMocks();
    const voice = fixture("Ask it to generate with proper JSON object.");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    await voice.session.submitText("Status?", "question");
    const initial = voice.request.mock.calls.map(([command]) => command).find(command => command.type === "submit");
    if (initial?.type !== "submit") throw new Error("Expected initial voice reply target");
    voice.emit({ type: "reply", sessionId: "session", messageId: "reply", utteranceId: initial.utteranceId,
      text: "I'm still listening. Do you want to add anything?", final: true });
    audio.playing = true;
    try {
      frames(4, 0.03); // "Awesome" over the end of the reply
      audio.playing = false;
      frames(60, 0.03); // then 1.2 s of clear speech
      frames(28, 0);
      await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
      expect(voice.sent("decide")[0]).toMatchObject({ text: "Ask it to generate with proper JSON object." });
    } finally {
      audio.playing = false;
      voice.session.close();
    }
  });

  it("plays a finished task's narration even after a newer status reply took the speaker", async () => {
    vi.clearAllMocks();
    const voice = fixture("Any update?");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    const pcm = new ArrayBuffer(2);
    const narration = { type: "audio", sessionId: "session", utteranceId: "task-u", replyId: "r", pcm,
      sampleRate: 24_000 } as const;
    voice.emit(narration);
    expect(audio.play).not.toHaveBeenCalled(); // an older utterance's audio stays muted...
    voice.emit({ type: "narrating", sessionId: "session", utteranceId: "task-u" });
    voice.emit(narration);
    expect(audio.play).toHaveBeenCalledOnce(); // ...until the host says it is narrating it
    voice.session.close();
  });

  it("does not start a narration over the user mid-sentence", async () => {
    vi.clearAllMocks();
    const voice = fixture("Any update?");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(8, 0.03); // speaking
    voice.emit({ type: "narrating", sessionId: "session", utteranceId: "task-u" });
    voice.emit({ type: "audio", sessionId: "session", utteranceId: "task-u", replyId: "r", pcm: new ArrayBuffer(2),
      sampleRate: 24_000 });
    expect(audio.play).not.toHaveBeenCalled();
    voice.session.close();
  });

  it("a short sound in a pause while a transcript is still decoding never strands the decision", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    const answer = voice.request.getMockImplementation()!;
    let release!: () => void;
    let held = false;
    voice.request.mockImplementation(async command => {
      if (command.type === "decode" && !command.final && !held) {
        held = true;
        await new Promise<void>(resolve => { release = resolve; });
        return { text: "Use SQLite for the tests" };
      }
      return answer(command);
    });
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(40, 0.03); // speech; a partial goes out and hangs
    await vi.waitFor(() => expect(held).toBe(true));
    frames(28, 0); // pause
    frames(6, 0.05); // a cough in the pause while that partial is still decoding
    frames(3, 0);
    release();
    await vi.waitFor(() => expect(voice.trace.mock.calls.some(([, e]) => e === "decision")).toBe(true));
    for (let i = 0; i < 10; i += 1) { frames(100, 0); await settle(); }
    expect(voice.sent("commit")).toHaveLength(1);
    voice.session.close();
  });

  it("noise with no words at all ends its turn instead of holding it open", async () => {
    vi.clearAllMocks();
    const voice = fixture("(cars honking)");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    for (let batch = 0; batch < 20; batch += 1) { frames(20, 0.05); await settle(); } // 8 s of traffic
    expect(voice.trace.mock.calls.some(([, event]) => event === "hold_for_words")).toBe(true);
    expect(voice.callbacks.state).toHaveBeenCalledWith(expect.objectContaining({ capturing: false }));
    expect(voice.sent("decide")).toHaveLength(0);
    voice.session.close();
  });

  it("a turn that began over a reply is watched for words once the reply has stopped", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite.");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    await voice.session.submitText("Status?", "question");
    const initial = voice.request.mock.calls.map(([command]) => command).find(command => command.type === "submit");
    if (initial?.type !== "submit") throw new Error("Expected initial voice reply target");
    voice.emit({ type: "reply", sessionId: "session", messageId: "reply", utteranceId: initial.utteranceId,
      text: "Here is an update.", final: true });
    audio.playing = true;
    try {
      frames(4, 0.03);
      audio.playing = false;
      for (let batch = 0; batch < 20; batch += 1) { frames(20, 0.05); await settle(); } // steady noise after
      expect(voice.trace.mock.calls.some(([, event]) => event === "hold_for_words")).toBe(true);
    } finally {
      audio.playing = false;
      voice.session.close();
    }
  });

  it("its own narration heard back through the microphone is recognised as echo", async () => {
    vi.clearAllMocks();
    const narration = "Done. The tests pass and the timer is fixed.";
    const voice = fixture(narration);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    voice.emit({ type: "narrating", sessionId: "session", utteranceId: "task-u" });
    voice.emit({ type: "speaking", sessionId: "session", utteranceId: "task-u", text: narration });
    audio.playing = true;
    try {
      frames(16, 0.03);
      frames(40, 0);
      await settle(); await settle();
      expect(audio.stopPlayback).not.toHaveBeenCalled(); // the narration did not stop itself
      expect(voice.sent("interrupt")).toHaveLength(0);
      expect(voice.sent("decide")).toHaveLength(0);
    } finally {
      audio.playing = false;
      voice.session.close();
    }
  });

  it("traces why playback stopped", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite.");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    audio.stopPlayback.mockReturnValueOnce(performance.now());
    voice.session.stopSpeaking();
    await settle();
    const interrupts = voice.trace.mock.calls.filter(([, event]) => event === "interrupt");
    expect(interrupts[0]?.[2]).toMatchObject({ reason: "stop_button", wasPlaying: true });
    voice.session.close();
  });

  it("with the speech detector running, loud noise never starts a turn", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    withDetector(voice, () => 0.03);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    for (let batch = 0; batch < 30; batch += 1) {
      frames(4, 0.08);
      await settle();
    }
    expect(voice.sent("snapshot")).toHaveLength(0);
    expect(voice.trace.mock.calls.some(([, event]) => event === "vad_on")).toBe(true);
    voice.session.close();
  });

  it("with the speech detector running, quiet speech starts a turn and is decided", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    let level = 0.9;
    withDetector(voice, () => level);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    for (let batch = 0; batch < 6; batch += 1) { frames(4, 0.004); await settle(); }
    await vi.waitFor(() => expect(voice.sent("snapshot")).toHaveLength(1));
    level = 0.02;
    for (let batch = 0; batch < 12; batch += 1) { frames(4, 0.08); await settle(); } // traffic, not speech
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    voice.session.close();
  });

  it("with the speech detector running, speech without new words is held only after six seconds", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    withDetector(voice, () => 0.9);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    const held = () => voice.trace.mock.calls.some(([, event]) => event === "hold_for_words");
    for (let batch = 0; batch < 60; batch += 1) { frames(4, 0.03); await settle(); } // ~4.8 s
    expect(held()).toBe(false);
    for (let batch = 0; batch < 40; batch += 1) { frames(4, 0.03); await settle(); } // ~8 s
    expect(held()).toBe(true);
    voice.session.close();
  });

  it("a detector that answers null falls back to loudness for the rest of the session", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    withDetector(voice, () => null);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(4, 0);
    await settle();
    expect(voice.trace.mock.calls.some(([, event]) => event === "vad_off")).toBe(true);
    frames(12, 0.03);
    expect(voice.sent("snapshot")).toHaveLength(1);
    expect(voice.sent("vad")).toHaveLength(1);
    voice.session.close();
  });

  it("traffic in a pause never drops the decision: the reply waits for the transcript, then goes ahead", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    let partials = 0;
    const answer = voice.request.getMockImplementation()!;
    voice.request.mockImplementation(command => {
      if (command.type === "decode" && !command.final) partials += 1;
      return answer(command);
    });
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.trace.mock.calls.some(([, event]) => event === "decision")).toBe(true));
    const before = partials;
    frames(6, 0.05); // a car horn
    frames(40, 0); // quiet long enough to act, but the horn is not transcribed yet
    expect(voice.sent("commit")).toHaveLength(0);
    await vi.waitFor(() => expect(partials).toBeGreaterThan(before));
    await settle();
    frames(1, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    expect(voice.sent("commit")[0]).toMatchObject({ decisionId: "d1" });
    expect(voice.sent("decide")).toHaveLength(1);
    expect(voice.trace.mock.calls.some(([, event]) => event === "resume")).toBe(false);
    voice.session.close();
  });

  it("sound that brings no new words for two seconds is treated as a pause, and only new words resume", async () => {
    vi.clearAllMocks();
    let heard = "Use SQLite for the tests";
    const voice = fixture(heard);
    const answer = voice.request.getMockImplementation()!;
    voice.request.mockImplementation(command => command.type === "decode"
      ? Promise.resolve({ text: heard }) : command.type === "decide"
        ? Promise.resolve({ turn: "thinking", action: "listen", say: "", request: "", decisionId: "d1" })
        : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(40, 0.03); // speech with words
    await vi.waitFor(() => expect(voice.trace.mock.calls.some(([, event]) => event === "transcript")).toBe(true));
    frames(110, 0.03); // traffic: loud, no new words
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    expect(voice.trace.mock.calls.some(([, event]) => event === "hold_for_words")).toBe(true);
    const resumesBefore = voice.trace.mock.calls.filter(([, event]) => event === "resume").length;
    frames(60, 0.03); // more traffic: still held
    await settle();
    expect(voice.trace.mock.calls.filter(([, event]) => event === "resume")).toHaveLength(resumesBefore);
    heard = "Use SQLite for the tests and also the Postgres migration";
    frames(30, 0.03);
    await vi.waitFor(() => expect(voice.trace.mock.calls.filter(([, event]) => event === "resume").length)
      .toBeGreaterThan(resumesBefore));
    voice.session.close();
  });

  it("a failed decision keeps listening, asks once more at the wait limit, then keeps the draft", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    const answer = voice.request.getMockImplementation()!;
    voice.request.mockImplementation(command => command.type === "decide"
      ? Promise.resolve({ refused: "voice_agent_unavailable", message: "Connect again in settings." }) : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ waiting: true }));
    expect(voice.callbacks.state).not.toHaveBeenCalledWith(expect.objectContaining({ reviews: expect.anything() }));
    expect(voice.callbacks.error).not.toHaveBeenCalled();
    frames(372, 0);
    await vi.waitFor(() => expect(voice.callbacks.state)
      .toHaveBeenCalledWith({ reviews: [{ id: expect.any(String), transcript: "Use SQLite for the tests" }] }));
    expect(voice.sent("decide").map(command => command.type === "decide" && command.finalCall)).toEqual([false, true]);
    expect(voice.callbacks.error).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("Connect again in settings."));
    expect(voice.sent("commit")).toHaveLength(0);
    voice.session.close();
  });

  it("a decision that fails once and succeeds on the final question is acted on", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    const answer = voice.request.getMockImplementation()!;
    let decides = 0;
    voice.request.mockImplementation(command => command.type === "decide" && ++decides === 1
      ? Promise.resolve({ refused: "voice_agent_unavailable" }) : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ waiting: true }));
    frames(372, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    expect(voice.callbacks.error).not.toHaveBeenCalled();
    voice.session.close();
  });

  it("shows that it is deciding while the voice agent thinks", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    const answer = voice.request.getMockImplementation()!;
    let release!: () => void;
    voice.request.mockImplementation(command => command.type === "decide"
      ? new Promise(resolve => { release = () => resolve(answer(command)); }) : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ checking: false, deciding: true }));
    release();
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ deciding: false, slowDecision: false }));
    voice.session.close();
  });

  it("a slow decision plays one quiet cue whose sound never counts as speech", async () => {
    vi.clearAllMocks();
    const voice = fixture("Use SQLite for the tests");
    const answer = voice.request.getMockImplementation()!;
    let release!: () => void;
    voice.request.mockImplementation(command => command.type === "decide"
      ? new Promise(resolve => { release = () => resolve(answer(command)); }) : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    await vi.waitFor(() => expect(audio.cue).toHaveBeenCalledOnce(), { timeout: 3_000 });
    expect(voice.callbacks.state).toHaveBeenCalledWith({ slowDecision: true });
    frames(5, 0.03); // the cue picked up by the microphone
    release();
    await settle();
    frames(60, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    expect(audio.cue).toHaveBeenCalledOnce();
    voice.session.close();
  });

  it.each(["Hmm.", "Yes.", "you"])("a single word or filler at a pause never decides: %s", async transcript => {
    vi.clearAllMocks();
    const voice = fixture(transcript);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(60, 0);
    await vi.waitFor(() => expect(voice.sent("decode").filter(command => command.type === "decode" && command.final)).toHaveLength(1));
    await settle();
    expect(voice.sent("decide")).toHaveLength(0);
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();
    voice.session.close();
  });

  it("a one-word answer to the assistant's own question reaches decide", async () => {
    vi.clearAllMocks();
    const voice = fixture("", { turn: "complete", action: "clarify", say: "Which file, the host or the session?", request: "" });
    const answer = voice.request.getMockImplementation()!;
    let finals = 0;
    voice.request.mockImplementation(command => command.type === "decode" && command.final
      ? Promise.resolve({ text: ++finals === 1 ? "Fix the timer please" : "Okay." })
      : command.type === "decode" ? Promise.resolve({ text: "" }) : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(60, 0);
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    voice.decide(REPLY);
    frames(12, 0.03);
    frames(60, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(2));
    expect(voice.sent("decide")[1]).toMatchObject({ text: "Okay." });
    voice.session.close();
  });

  it("the wait limit hands a slow pause decode to a final decision instead of dropping it", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    const answer = voice.request.getMockImplementation()!;
    let release!: (value: { text: string }) => void;
    voice.request.mockImplementation(command => command.type === "decode" && command.final
      ? new Promise(resolve => { release = resolve; })
      : command.type === "decode" ? Promise.resolve({ text: "" }) : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(400, 0);
    await vi.waitFor(() => expect(release).toBeDefined());
    release({ text: "Use SQLite for the tests" });
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    expect(voice.sent("decide")).toEqual([expect.objectContaining({ text: "Use SQLite for the tests", finalCall: true })]);
    voice.session.close();
  });

  it("resumed speech forgets the earlier pause's text before the wait limit", async () => {
    vi.clearAllMocks();
    const voice = fixture("", { turn: "thinking", action: "listen", say: "", request: "" });
    const answer = voice.request.getMockImplementation()!;
    let finals = 0, release!: (value: { text: string }) => void;
    voice.request.mockImplementation(command => {
      if (command.type === "decode" && command.final) {
        return ++finals === 1 ? Promise.resolve({ text: "Add a readme" }) : new Promise(resolve => { release = resolve; });
      }
      return command.type === "decode" ? Promise.resolve({ text: finals ? "Add a readme with a license" : "" })
        : answer(command);
    });
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(1));
    await settle();
    frames(10, 0.03);
    frames(1, 0);
    await vi.waitFor(() => expect(voice.trace.mock.calls.some(([, event]) => event === "words_resumed")).toBe(true));
    frames(400, 0);
    await vi.waitFor(() => expect(release).toBeDefined());
    await settle();
    expect(voice.sent("decide")).toHaveLength(1);
    voice.decide({ turn: "complete", action: "start_task", say: "On it.", request: "x" });
    release({ text: "Add a readme with a license section" });
    await vi.waitFor(() => expect(voice.sent("decide")).toHaveLength(2));
    expect(voice.sent("decide")[1]).toMatchObject({ text: "Add a readme with a license section", finalCall: true });
    voice.session.close();
  });

  it("a failed pause decode keeps the partial transcript for review at the wait limit", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    const answer = voice.request.getMockImplementation()!;
    voice.request.mockImplementation(command => command.type === "decode" && command.final
      ? Promise.reject(new Error("Transcription timed out"))
      : command.type === "decode" ? Promise.resolve({ text: "Use SQLite for the tests" }) : answer(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(40, 0.03);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ partial: "Use SQLite for the tests" }));
    frames(400, 0);
    await vi.waitFor(() => expect(voice.callbacks.state)
      .toHaveBeenCalledWith({ reviews: [{ id: expect.any(String), transcript: "Use SQLite for the tests" }] }));
    expect(voice.callbacks.error).toHaveBeenCalledOnce();
    expect(voice.sent("decide")).toHaveLength(0);
    voice.session.close();
  });

  it("a push-to-talk send right after a rollover keeps the rolled-over speech", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    const answer = voice.request.getMockImplementation()!;
    voice.request.mockImplementation(command => command.type === "decode" && command.final
      ? Promise.resolve({ text: "Rewrite the whole narration path" })
      : command.type === "decode" ? Promise.resolve({ text: "" }) : answer(command));
    voice.session.setPushToTalk(true);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.setMicrophoneEnabled).toHaveBeenLastCalledWith(false));
    voice.session.beginHold();
    frames(1450, 0.03);
    frames(75, 0);
    voice.session.sendNow();
    await vi.waitFor(() => expect(voice.sent("commit")).toHaveLength(1));
    expect(voice.sent("decide")).toEqual([expect.objectContaining({ text: "Rewrite the whole narration path", finalCall: true })]);
    voice.session.close();
  });

  it("closes a session that finishes starting after the renderer has been disposed", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    let complete = () => {};
    voice.request.mockImplementationOnce(() => new Promise(resolve => {
      complete = () => resolve({ sessionId: "late" });
    }));
    const pending = voice.session.start("thread", "whisper-small", "system");
    voice.session.close();
    complete();
    await pending;
    expect(voice.request).toHaveBeenLastCalledWith({ type: "stop", sessionId: "late" });
    expect(audio.openMicrophone).not.toHaveBeenCalled();
  });
});
