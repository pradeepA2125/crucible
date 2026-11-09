import { describe, expect, it, vi } from "vitest";
import type { VoiceBridge, VoiceCommand, VoiceEvent } from "./voiceBridge";
import { VoiceSession } from "./voiceSession";
import { CAPTURE_FRAME_BYTES, type CaptureFrame } from "./speechSegments";

const audio = vi.hoisted(() => ({
  playing: false,
  play: vi.fn(), stopPlayback: vi.fn<() => number | undefined>(), close: vi.fn(), closeMicrophone: vi.fn(),
  setMicrophoneEnabled: vi.fn(),
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
    openMicrophone = audio.openMicrophone;
    resume = audio.resume;
  },
}));

function fixture(transcript = "Use SQLite") {
  let subscriber: ((event: VoiceEvent) => void) | undefined;
  const unsubscribe = vi.fn(() => { subscriber = undefined; });
  const request = vi.fn(async (command: VoiceCommand): Promise<unknown> => command.type === "start"
    ? { sessionId: "session" }
    : command.type === "decode" ? { text: transcript } : {});
  const bridge: VoiceBridge = { request, subscribe: callback => { subscriber = callback; return unsubscribe; } };
  const callbacks = {
    state: vi.fn(), utterance: vi.fn(), reply: vi.fn(), receipt: vi.fn(), error: vi.fn(), ended: vi.fn(),
  };
  return {
    request, unsubscribe, callbacks, session: new VoiceSession(bridge, callbacks),
    emit(event: VoiceEvent) { subscriber?.(event); },
  };
}

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
      type: "submit", sessionId: "session", utteranceId: snapshot.utteranceId, text: "Use SQLite", intent: "auto",
    }));

    voice.session.mute();
    feed(8, 0.03);
    feed(28, 0);
    expect(voice.request.mock.calls.filter(([command]) => command.type === "snapshot")).toHaveLength(1);
    expect(voice.request.mock.calls.filter(([command]) => command.type === "submit")).toHaveLength(1);
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
    await vi.waitFor(() => expect(commands()).toContain("submit"));
    feed(10, 0.03);
    expect(commands().filter(command => command === "snapshot")).toHaveLength(1);
    voice.session.beginHold();
    feed(10, 0.03);
    voice.session.cancelHold();
    voice.session.sendNow();
    expect(commands().filter(command => command === "submit")).toHaveLength(1);
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
    frames(125, 0);
    await vi.waitFor(() => expect(voice.callbacks.utterance)
      .toHaveBeenCalledExactlyOnceWith(expect.any(String), "Use SQLite instead."));
    expect(voice.request.mock.calls.filter(([command]) => command.type === "snapshot")).toHaveLength(1);
    voice.session.close();
  });

  it("keeps an uncertain transcript for review without speaking a repeat prompt", async () => {
    vi.clearAllMocks();
    const voice = fixture("Switch to the");
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(28, 0);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ partial: "Switch to the" }));
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();
    frames(197, 0);
    await vi.waitFor(() => expect(voice.callbacks.state)
      .toHaveBeenCalledWith({ reviews: [{ id: expect.any(String), transcript: "Switch to the" }] }));
    expect(voice.request).not.toHaveBeenCalledWith(expect.objectContaining({ type: "clarify" }));
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();
    const id = voice.callbacks.state.mock.lastCall?.[0].reviews[0].id;
    voice.session.sendReviewed(id);
    voice.session.sendReviewed(id);
    await vi.waitFor(() => expect(voice.callbacks.utterance).toHaveBeenCalledExactlyOnceWith(expect.any(String), "Switch to the"));
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
    frames(225, 0);
    await vi.waitFor(() => expect(voice.callbacks.state)
      .toHaveBeenLastCalledWith({ capturing: false, checking: false, partial: "" }));
    expect(voice.request).not.toHaveBeenCalledWith(expect.objectContaining({ type: "clarify" }));
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();

    voice.request.mockImplementation(request);
    frames(12, 0.03);
    frames(125, 0);
    await vi.waitFor(() => expect(voice.callbacks.utterance)
      .toHaveBeenCalledExactlyOnceWith(expect.any(String), "Use SQLite"));
    voice.session.close();
  });

  it("keeps a thought open when the speaker resumes after two seconds", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    const defaultRequest = voice.request.getMockImplementation()!;
    let text = "Use Postgres for the test.";
    voice.request.mockImplementation(command => command.type === "decode"
      ? Promise.resolve({ text }) : defaultRequest(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(100, 0);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ partial: text }));
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();
    text = "Use Postgres for the test, actually use SQLite instead.";
    frames(15, 0.03);
    frames(125, 0);
    await vi.waitFor(() => expect(voice.callbacks.utterance).toHaveBeenCalledExactlyOnceWith(expect.any(String), text));
    expect(voice.request.mock.calls.filter(([command]) => command.type === "snapshot")).toHaveLength(1);
    voice.session.close();
  });

  it.each(["Use SQLite.", "What does the greeting function return?"])("sends a stable transcript after the pause settles: %s", async transcript => {
    vi.clearAllMocks();
    const voice = fixture(transcript);
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(75, 0);
    const finalDecodes = () => voice.request.mock.calls.filter(([command]) => command.type === "decode" && command.final);
    await vi.waitFor(() => expect(finalDecodes()).toHaveLength(2));
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();
    frames(50, 0);
    await vi.waitFor(() => expect(voice.callbacks.utterance).toHaveBeenCalledExactlyOnceWith(expect.any(String), transcript));
    expect(voice.request).not.toHaveBeenCalledWith(expect.objectContaining({ type: "clarify" }));
    voice.session.close();
  });

  it("does not send when a borderline transcript changes during the recheck", async () => {
    vi.clearAllMocks();
    const voice = fixture("Run the migration now.");
    const defaultRequest = voice.request.getMockImplementation()!;
    let decodes = 0;
    voice.request.mockImplementation(command => command.type === "decode"
      ? Promise.resolve({ text: ++decodes === 1 ? "Run the migration now." : "Do not run the migration now." })
      : defaultRequest(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(75, 0);
    await vi.waitFor(() => expect(decodes).toBe(2));
    frames(150, 0);
    await vi.waitFor(() => expect(voice.callbacks.state)
      .toHaveBeenCalledWith({ reviews: [{ id: expect.any(String), transcript: "Run the migration now." }] }));
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();
    expect(voice.request).not.toHaveBeenCalledWith(expect.objectContaining({ type: "clarify" }));
    voice.session.close();
  });

  it("waits for an in-flight second transcription before prompting or sending", async () => {
    vi.clearAllMocks();
    const transcript = "What does the greeting function return?";
    const voice = fixture(transcript);
    const defaultRequest = voice.request.getMockImplementation()!;
    let decodes = 0;
    let complete!: (result: { text: string }) => void;
    voice.request.mockImplementation(command => command.type === "decode" && ++decodes === 2
      ? new Promise(resolve => { complete = resolve; })
      : defaultRequest(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(225, 0);
    await vi.waitFor(() => expect(decodes).toBe(2));
    expect(voice.request).not.toHaveBeenCalledWith(expect.objectContaining({ type: "clarify" }));
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();
    complete({ text: transcript });
    await vi.waitFor(() => expect(voice.callbacks.utterance).toHaveBeenCalledExactlyOnceWith(expect.any(String), transcript));
    expect(voice.request).not.toHaveBeenCalledWith(expect.objectContaining({ type: "clarify" }));
    voice.session.close();
  });

  it("holds the request when the recognizer changes a filename at the pause", async () => {
    vi.clearAllMocks();
    const voice = fixture();
    const defaultRequest = voice.request.getMockImplementation()!;
    voice.request.mockImplementation(command => command.type === "decode"
      ? Promise.resolve({ text: command.final ? "Read draft vault.tsb" : "ReadDraftVault.com" })
      : defaultRequest(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(40, 0.03);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ partial: "ReadDraftVault.com" }));
    frames(40, 0);
    await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ partial: "Read draft vault.tsb" }));
    expect(voice.callbacks.utterance).not.toHaveBeenCalled();
    frames(185, 0);
    await vi.waitFor(() => expect(voice.callbacks.state)
      .toHaveBeenCalledWith({ reviews: [{ id: expect.any(String), transcript: "Read draft vault.tsb" }] }));
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
        frames(197, 0);
        await vi.waitFor(() => expect(voice.callbacks.state)
          .toHaveBeenCalledWith({ reviews: [{ id: expect.any(String), transcript }] }));
        expect(voice.request).not.toHaveBeenCalledWith(expect.objectContaining({ type: "clarify" }));
      } else {
        await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenCalledWith({ capturing: false, checking: false, partial: "" }));
      }
      const spoken = voice.request.mock.calls.map(([command]) => command)
        .filter(command => command.type === "submit" && command.intent === "auto");
      expect(spoken).toHaveLength(submitted ? 1 : 0);
      if (submitted) expect(spoken[0]).toMatchObject({ text: transcript });
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
      frames(8, 0.03);
      frames(225, 0);
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
      ? Promise.reject(new Error("The coding turn changed.")) : request(command));
    await voice.session.start("thread", "whisper-small", "system");
    await vi.waitFor(() => expect(audio.openMicrophone).toHaveBeenCalledOnce());
    frames(12, 0.03);
    frames(225, 0);
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
      await vi.waitFor(() => expect(voice.callbacks.state).toHaveBeenLastCalledWith({ capturing: false, checking: false, partial: "" }));
      expect(voice.request.mock.calls.filter(([command]) => command.type === "submit")).toHaveLength(1);
    } finally {
      audio.playing = false;
      voice.session.close();
    }
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
