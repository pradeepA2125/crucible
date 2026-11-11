import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceHost, type VoiceSnapshot, type VoiceThread } from "../src/voice/host.js";

const mocks = vi.hoisted(() => ({
  vadPath: vi.fn(async (): Promise<string> => "/verified/vad.bin"),
  detect: vi.fn(async (_pcm: ArrayBuffer, _signal: AbortSignal): Promise<number[]> => [0.91, 0.12]),
  speak: vi.fn<(text: string, audio: (pcm: ArrayBuffer, sampleRate: number) => void, signal: AbortSignal) => Promise<void>>(),
  recognizer: vi.fn<(path: string, model: string, failure: (error: Error) => void, prompt?: string) => void>(),
}));

vi.mock("../src/voice/assets.js", () => ({
  VoiceAssets: class {
    async installed() { return ["whisper-small"]; }
    async pocketInstalled() { return false; }
    async verifiedPath() { return "/verified/model.bin"; }
    verifiedVadPath = mocks.vadPath;
    async cancelAndWait() {}
  },
}));
vi.mock("../src/voice/workers.js", () => ({
  Recognizer: class {
    constructor(...args: [string, string, (error: Error) => void, string?]) { mocks.recognizer(...args); }
    async wait() {}
    close() {}
  },
  Detector: class {
    get usable() { return true; }
    async wait() {}
    detect = mocks.detect;
    close() {}
  },
  Synthesizer: class {
    async wait() {}
    speak = mocks.speak;
    close() {}
  },
}));

const directories: string[] = [];
const sessions: VoiceHost[] = [];
const active = (turnId: string): VoiceSnapshot => ({ active: true, turnId, processId: "process-1" });
afterEach(async () => {
  await Promise.all(sessions.splice(0).map(session => session.dispose()));
  mocks.speak.mockReset();
  mocks.recognizer.mockReset();
  await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true })));
});

async function fixture(first: VoiceSnapshot, options: {
  voiceModel?: string | null; vocabulary?: VoiceThread["vocabulary"];
} = {}) {
  const path = await mkdtemp(join(tmpdir(), "crucible-voice-host-"));
  directories.push(path);
  await Promise.all(["crucible-whisper", "crucible-tts"].map(file => writeFile(join(path, file), "", { mode: 0o700 })));
  let snapshot = first;
  const sendIdle = vi.fn<VoiceThread["sendIdle"]>().mockResolvedValue(undefined);
  const submit = vi.fn<VoiceThread["submit"]>().mockImplementation(async request => ({
    message_id: request.message_id, turn_id: request.expected_turn_id, kind: "reply",
    status: "answered", text: "Still working.",
  }));
  const decide = vi.fn<VoiceThread["decide"]>().mockResolvedValue({
    decisionId: "d1", turn: "complete", action: "reply", say: "It's going well.", request: "", turnId: null,
  });
  const commit = vi.fn<VoiceThread["commit"]>().mockResolvedValue({ action: "reply" });
  const narrate = vi.fn<VoiceThread["narrate"]>().mockResolvedValue("Done. The tests pass.");
  const thread: VoiceThread = {
    current: () => true, snapshot: async () => snapshot, submit, sendIdle, receipt: async () => null,
    decide, commit, narrate,
    vocabulary: options.vocabulary ?? (async () => []),
  };
  const events: Record<string, unknown>[] = [];
  const traces: { source: string; event: string; data: Record<string, unknown> }[] = [];
  const host = new VoiceHost({
    profile: path, helpers: path, platform: "darwin", architecture: "arm64",
    thread: async () => thread, publish: event => events.push(event),
    diagnostics: { write: (source, event, data) => traces.push({ source, event, data }) },
  });
  sessions.push(host);
  const started = await host.request({ type: "start", threadId: "thread",
    stt: "whisper-small", tts: "system", ...(options.voiceModel !== undefined ? { voiceModel: options.voiceModel } : {}) },
  ) as { sessionId: string; vad?: boolean };
  const { sessionId } = started;
  return { host, sessionId, started, thread, submit, sendIdle, decide, commit, narrate, events, traces,
    setSnapshot: (value: VoiceSnapshot) => { snapshot = value; } };
}

describe("project words for speech recognition", () => {
  it("start primes the recognizer with the project's names", async () => {
    await fixture(active("turn-a"), { vocabulary: async () => ["crucible", "ControllerLoop"] });
    expect(mocks.recognizer).toHaveBeenCalledWith(expect.stringContaining("crucible-whisper"),
      "/verified/model.bin", expect.any(Function), "crucible, ControllerLoop");
  });

  it("a vocabulary failure starts the recognizer without project words", async () => {
    await fixture(active("turn-a"), { vocabulary: async () => { throw new Error("offline"); } });
    expect(mocks.recognizer.mock.calls[0]?.[3]).toBeUndefined();
  });

  it("a slow vocabulary is not waited for beyond its limit", async () => {
    const started = performance.now();
    await fixture(active("turn-a"), { vocabulary: signal => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(signal.reason));
    }) });
    expect(mocks.recognizer.mock.calls[0]?.[3]).toBeUndefined();
    expect(performance.now() - started).toBeLessThan(4000);
  });
});

describe("desktop voice admission", () => {
  it("keeps an active utterance tied to its original turn and submits it once", async () => {
    mocks.speak.mockResolvedValue(undefined);
    const { host, sessionId, submit, sendIdle, setSnapshot, events } = await fixture(active("turn-a"));
    await host.request({ type: "snapshot", sessionId, utteranceId: "utterance-a" });
    setSnapshot(active("turn-b"));
    const message = { type: "submit", sessionId, utteranceId: "utterance-a",
      text: "How is it going?", intent: "question" } as const;
    await host.request(message);
    await expect(host.request(message)).rejects.toThrow("no longer open");
    expect(submit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      expected_turn_id: "turn-a", expected_process_id: "process-1", intent: "question",
    }), expect.any(AbortSignal));
    expect(sendIdle).not.toHaveBeenCalled();
    expect(events).toContainEqual(expect.objectContaining({ type: "receipt" }));
  });

  it("refuses an idle utterance when a coding turn began before admission", async () => {
    const { host, sessionId, submit, sendIdle, setSnapshot } = await fixture({ active: false, turnId: null, processId: "process-1" });
    await host.request({ type: "snapshot", sessionId, utteranceId: "utterance-a" });
    setSnapshot(active("turn-b"));
    await expect(host.request({ type: "submit", sessionId, utteranceId: "utterance-a",
      text: "Do the next step.", intent: "auto" })).rejects.toThrow("coding turn changed");
    expect(submit).not.toHaveBeenCalled();
    expect(sendIdle).not.toHaveBeenCalled();
  });

  it("discards generated audio after barge-in while preserving the backend receipt", async () => {
    let produceAudio: ((pcm: ArrayBuffer, sampleRate: number) => void) | undefined;
    mocks.speak.mockImplementation(async (_text, audio) => { produceAudio = audio; });
    const { host, sessionId, events } = await fixture(active("turn-a"));
    await host.request({ type: "snapshot", sessionId, utteranceId: "utterance-a" });
    await host.request({ type: "submit", sessionId, utteranceId: "utterance-a", text: "Status?", intent: "auto" });
    expect(produceAudio).toBeDefined();
    produceAudio!(new ArrayBuffer(2), 24000);
    await host.request({ type: "interrupt", sessionId });
    produceAudio!(new ArrayBuffer(2), 24000);
    expect(events.filter(event => event.type === "audio")).toHaveLength(1);
    expect(events).toContainEqual(expect.objectContaining({ type: "receipt" }));
  });

  it("preserves the captured turn while the transcript waits for review", async () => {
    mocks.speak.mockResolvedValue(undefined);
    const { host, sessionId, submit, sendIdle, setSnapshot } = await fixture(active("turn-a"));
    await host.request({ type: "snapshot", sessionId, utteranceId: "review" });
    expect(mocks.speak).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
    expect(sendIdle).not.toHaveBeenCalled();
    setSnapshot(active("turn-b"));
    await host.request({ type: "submit", sessionId, utteranceId: "review", text: "Use SQLite.", intent: "auto" });
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({ expected_turn_id: "turn-a" }), expect.any(AbortSignal));
  });
});

const idle: VoiceSnapshot = { active: false, turnId: null, processId: "process-1" };
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const refusal = (status: number, code: string) => Object.assign(new Error(code), { status, detail: { code } });

describe("desktop voice front-man", () => {
  it("decide forwards to the thread and returns the decision", async () => {
    const { host, sessionId, decide } = await fixture(idle);
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    const decision = await host.request({ type: "decide", sessionId, utteranceId: "u1",
      text: "how is it going", silenceMs: 700, finalCall: false });
    expect(decision).toEqual({ decisionId: "d1", turn: "complete", action: "reply", say: "It's going well.", request: "" });
    expect(decide).toHaveBeenCalledExactlyOnceWith({ utterance: "how is it going", silenceMs: 700, finalCall: false,
      expectedProcessId: "process-1" }, expect.any(AbortSignal));
  });

  it("decide reports an unavailable voice agent as a refusal", async () => {
    const { host, sessionId, decide } = await fixture(idle);
    decide.mockRejectedValueOnce(refusal(503, "voice_agent_unavailable"));
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    expect(await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "hello", silenceMs: 700,
      finalCall: false })).toEqual({ refused: "voice_agent_unavailable" });
  });

  it("decide passes the voice agent's own reason with the refusal", async () => {
    const { host, sessionId, decide } = await fixture(idle);
    decide.mockRejectedValueOnce(Object.assign(new Error("x"), {
      status: 503, detail: { code: "voice_agent_unavailable", message: "Connect again in Crucible settings." } }));
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    expect(await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "hello", silenceMs: 700,
      finalCall: false })).toEqual({ refused: "voice_agent_unavailable", message: "Connect again in Crucible settings." });
  });

  it("records what the app traced, and the decide and commit it carried out", async () => {
    mocks.speak.mockResolvedValue(undefined);
    const { host, sessionId, traces } = await fixture(idle);
    await host.request({ type: "trace", sessionId, event: "resume", data: { quietMs: 620, rms: 0.006 } });
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "how is it going", silenceMs: 700,
      finalCall: false });
    await host.request({ type: "commit", sessionId, utteranceId: "u1", decisionId: "d1" });
    expect(traces.find(trace => trace.source === "app"))
      .toEqual({ source: "app", event: "resume", data: { quietMs: 620, rms: 0.006 } });
    expect(traces.find(trace => trace.event === "decide")).toMatchObject({ source: "host", data: {
      utteranceId: "u1", text: "how is it going", silenceMs: 700, finalCall: false, turn: "complete",
      action: "reply", say: "It's going well.", ms: expect.any(Number) } });
    expect(traces.find(trace => trace.event === "commit")).toMatchObject({ source: "host", data: {
      utteranceId: "u1", decisionId: "d1", action: "reply" } });
    await vi.waitFor(() => expect(traces.find(trace => trace.event === "speak")).toMatchObject({
      source: "host", data: { chars: expect.any(Number), ms: expect.any(Number) } }));
  });

  it("records a refused decision with its reason", async () => {
    const { host, sessionId, decide, traces } = await fixture(idle);
    decide.mockRejectedValueOnce(Object.assign(new Error("x"), {
      status: 503, detail: { code: "voice_agent_unavailable", message: "timed out" } }));
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "hello", silenceMs: 700, finalCall: true });
    expect(traces.find(trace => trace.event === "decide")).toMatchObject({ data: {
      refused: "voice_agent_unavailable", reason: "timed out", finalCall: true } });
  });

  it("the voice model chosen at start, then changed, rides each decide", async () => {
    const { host, decide } = await fixture(idle, { voiceModel: "gpt-6-luna" });
    const sessionId = (host as unknown as { session: { id: string } }).session.id;
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "hi", silenceMs: 700, finalCall: false });
    expect(decide.mock.calls[0]?.[0]).toMatchObject({ voiceModel: "gpt-6-luna" });
    await host.request({ type: "voiceModel", sessionId, model: null });
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "hi", silenceMs: 700, finalCall: false });
    expect(decide.mock.calls[1]?.[0]).not.toHaveProperty("voiceModel");
  });

  it("narration uses the session's voice model", async () => {
    const { hooks, narrate } = await startedTask(undefined, "gpt-6.1-terra");
    hooks.chunk("Done.");
    hooks.finish();
    await vi.waitFor(() => expect(narrate).toHaveBeenCalledWith("fix it", "Done.", "process-1",
      expect.any(AbortSignal), "gpt-6.1-terra"));
  });

  it("start says whether the speech detector is running", async () => {
    expect((await fixture(idle)).started).toMatchObject({ vad: true });
    mocks.vadPath.mockRejectedValueOnce(new Error("missing"));
    expect((await fixture(idle)).started).toMatchObject({ vad: false });
  });

  it("vad returns the detector's speech probabilities for the session's audio", async () => {
    const { host, sessionId } = await fixture(idle);
    expect(await host.request({ type: "vad", sessionId, pcm: new ArrayBuffer(2560) }))
      .toEqual({ probs: [0.91, 0.12] });
    expect(mocks.detect).toHaveBeenCalledWith(expect.any(ArrayBuffer), expect.any(AbortSignal));
  });

  it("without a verified VAD model the session still starts and vad answers null", async () => {
    mocks.vadPath.mockRejectedValueOnce(new Error("Install the selected voice model first."));
    const { host, sessionId, traces } = await fixture(idle);
    expect(await host.request({ type: "vad", sessionId, pcm: new ArrayBuffer(2560) })).toEqual({ probs: null });
    expect(traces.find(trace => trace.event === "vad")).toMatchObject({ data: { available: false } });
  });

  it("a detector error answers null instead of failing the session", async () => {
    mocks.detect.mockRejectedValueOnce(new Error("behind"));
    const { host, sessionId } = await fixture(idle);
    expect(await host.request({ type: "vad", sessionId, pcm: new ArrayBuffer(2560) })).toEqual({ probs: null });
  });

  it("commit reply speaks say once through speakable", async () => {
    mocks.speak.mockResolvedValue(undefined);
    const { host, sessionId, decide, commit, sendIdle } = await fixture(idle);
    decide.mockResolvedValueOnce({ decisionId: "d1", turn: "complete", action: "reply",
      say: "Use `is_memory_enabled`.", request: "", turnId: null });
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "which flag", silenceMs: 700, finalCall: false });
    expect(await host.request({ type: "commit", sessionId, utteranceId: "u1", decisionId: "d1" })).toEqual({ action: "reply" });
    await flush();
    expect(commit).toHaveBeenCalledExactlyOnceWith("d1", "process-1", expect.any(AbortSignal));
    expect(mocks.speak).toHaveBeenCalledExactlyOnceWith("Use is memory enabled.", expect.any(Function), expect.any(AbortSignal));
    expect(sendIdle).not.toHaveBeenCalled();
    await expect(host.request({ type: "commit", sessionId, utteranceId: "u1", decisionId: "d1" })).rejects.toThrow("no longer open");
  });

  it("commit start_task sends the exact words with voiceRequest", async () => {
    mocks.speak.mockResolvedValue(undefined);
    const { host, sessionId, decide, commit, sendIdle } = await fixture(idle);
    decide.mockResolvedValueOnce({ decisionId: "d1", turn: "complete", action: "start_task", say: "On it.",
      request: "Add a README", turnId: null });
    commit.mockResolvedValueOnce({ action: "start_task", message: "add a, um, readme", voiceRequest: "Add a README" });
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "add a, um, readme", silenceMs: 700, finalCall: false });
    expect(await host.request({ type: "commit", sessionId, utteranceId: "u1", decisionId: "d1" })).toEqual({ action: "start_task" });
    expect(sendIdle).toHaveBeenCalledExactlyOnceWith("add a, um, readme", "Add a README", expect.any(Object));
    await flush();
    expect(mocks.speak).toHaveBeenCalledWith("On it.", expect.any(Function), expect.any(AbortSignal));
  });

  it("commit start_task maps 409 turn_active to a refusal and keeps the utterance open", async () => {
    const { host, sessionId, decide, commit, sendIdle } = await fixture(idle);
    decide.mockResolvedValue({ decisionId: "d1", turn: "complete", action: "start_task", say: "On it.",
      request: "Add a README", turnId: null });
    commit.mockRejectedValueOnce(refusal(409, "turn_active"));
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "add a readme", silenceMs: 700, finalCall: false });
    expect(await host.request({ type: "commit", sessionId, utteranceId: "u1", decisionId: "d1" })).toEqual({ refused: "turn_active" });
    expect(sendIdle).not.toHaveBeenCalled();
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "add a readme", silenceMs: 8000, finalCall: true });
    expect(decide).toHaveBeenCalledTimes(2);
  });

  it("commit steer publishes the receipt for the decided turn and speaks the acknowledgement", async () => {
    mocks.speak.mockResolvedValue(undefined);
    const { host, sessionId, decide, commit, events } = await fixture(active("turn-a"));
    decide.mockResolvedValueOnce({ decisionId: "d1", turn: "complete", action: "steer", say: "Got it.",
      request: "Use SQLite", turnId: "turn-a" });
    const receipt = { message_id: "d1", turn_id: "turn-a", kind: "steer", text: "Queued.", status: "incorporated" } as const;
    commit.mockResolvedValueOnce({ action: "steer", receipt });
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "use sqlite", silenceMs: 700, finalCall: false });
    await host.request({ type: "commit", sessionId, utteranceId: "u1", decisionId: "d1" });
    await flush();
    // The panel attaches receipts to the spoken utterance, not to the decision.
    expect(events).toContainEqual({ type: "receipt", sessionId, receipt: { ...receipt, message_id: "u1" } });
    expect(mocks.speak).toHaveBeenCalledExactlyOnceWith("Got it.", expect.any(Function), expect.any(AbortSignal));
  });

  it("announces the text of everything it speaks, so the app can recognise its echo", async () => {
    const { hooks, events } = await startedTask();
    hooks.chunk("Done. The tests pass.");
    hooks.finish();
    await vi.waitFor(() => expect(events).toContainEqual(expect.objectContaining({
      type: "speaking", utteranceId: "u1", text: "Done. The tests pass." })));
  });

  it("a queued voice correction reports its later status on the spoken utterance", async () => {
    mocks.speak.mockResolvedValue(undefined);
    const { host, sessionId, decide, commit, events, thread } = await fixture(active("turn-a"));
    decide.mockResolvedValueOnce({ decisionId: "d1", turn: "complete", action: "steer", say: "Got it.",
      request: "Use SQLite", turnId: "turn-a" });
    const queued = { message_id: "d1", turn_id: "turn-a", kind: "steer", text: "Queued.", status: "queued" } as const;
    commit.mockResolvedValueOnce({ action: "steer", receipt: queued });
    thread.receipt = async () => ({ ...queued, status: "expired", text: "That correction expired." });
    await host.request({ type: "snapshot", sessionId, utteranceId: "u1" });
    await host.request({ type: "decide", sessionId, utteranceId: "u1", text: "use sqlite", silenceMs: 700, finalCall: false });
    await host.request({ type: "commit", sessionId, utteranceId: "u1", decisionId: "d1" });
    await vi.waitFor(() => expect(events).toContainEqual({ type: "receipt", sessionId,
      receipt: { ...queued, status: "expired", text: "That correction expired.", message_id: "u1" } }), { timeout: 3_000 });
  });

  async function startedTask(narrate?: (heard: string) => Promise<string>, voiceModel?: string) {
    mocks.speak.mockResolvedValue(undefined);
    const harness = await fixture(idle, voiceModel ? { voiceModel } : {});
    if (narrate) harness.narrate.mockImplementation(heard => narrate(heard));
    harness.decide.mockResolvedValueOnce({ decisionId: "d1", turn: "complete", action: "start_task", say: "On it.",
      request: "Fix it", turnId: null });
    harness.commit.mockResolvedValueOnce({ action: "start_task", message: "fix it", voiceRequest: "Fix it" });
    await harness.host.request({ type: "snapshot", sessionId: harness.sessionId, utteranceId: "u1" });
    await harness.host.request({ type: "decide", sessionId: harness.sessionId, utteranceId: "u1", text: "fix it",
      silenceMs: 700, finalCall: false });
    await harness.host.request({ type: "commit", sessionId: harness.sessionId, utteranceId: "u1", decisionId: "d1" });
    const hooks = harness.sendIdle.mock.calls[0]?.[2];
    if (!hooks) throw new Error("Expected the task to be sent");
    await flush();
    mocks.speak.mockClear();
    return { ...harness, hooks };
  }

  it("idle answer chunks are shown, not spoken, and the narration is spoken at finish", async () => {
    const { hooks, events, narrate } = await startedTask();
    hooks.chunk("## Fixed\n- Edited `host.ts`.\n");
    hooks.chunk("- Tests pass.");
    await flush();
    expect(mocks.speak).not.toHaveBeenCalled();
    expect(events.filter(event => event.type === "reply").at(-1)).toMatchObject({ text: "## Fixed\n- Edited `host.ts`.\n- Tests pass." });
    hooks.finish();
    await vi.waitFor(() => expect(mocks.speak).toHaveBeenCalledOnce());
    expect(narrate).toHaveBeenCalledExactlyOnceWith("fix it", "## Fixed\n- Edited `host.ts`.\n- Tests pass.",
      "process-1", expect.any(AbortSignal), undefined);
    expect(mocks.speak).toHaveBeenCalledWith("Done. The tests pass.", expect.any(Function), expect.any(AbortSignal));
  });

  it("narrate failure speaks the first two sentences and the on-screen note", async () => {
    const { hooks } = await startedTask(async () => { throw new Error("down"); });
    hooks.chunk("Fixed the timer. Edited `host.ts`. Tests pass. More detail.");
    hooks.finish();
    await vi.waitFor(() => expect(mocks.speak).toHaveBeenCalledOnce());
    expect(mocks.speak).toHaveBeenCalledWith("Fixed the timer. Edited host. The rest is on screen.",
      expect.any(Function), expect.any(AbortSignal));
  });

  it("a status reply while the task runs neither drops its answer nor cancels its narration", async () => {
    const { hooks, host, sessionId, events } = await startedTask();
    await host.request({ type: "snapshot", sessionId, utteranceId: "u2" });
    await host.request({ type: "decide", sessionId, utteranceId: "u2", text: "any update?", silenceMs: 700,
      finalCall: false });
    await host.request({ type: "commit", sessionId, utteranceId: "u2", decisionId: "d1" });
    await vi.waitFor(() => expect(mocks.speak).toHaveBeenCalledWith("It's going well.", expect.any(Function),
      expect.any(AbortSignal)));
    await host.request({ type: "interrupt", sessionId }); // the user stops that status reply
    hooks.chunk("Done. The tests pass.");
    hooks.finish();
    await vi.waitFor(() => expect(mocks.speak).toHaveBeenCalledWith("Done. The tests pass.", expect.any(Function),
      expect.any(AbortSignal)));
    expect(events.filter(event => event.type === "reply" && event.utteranceId === "u1").at(-1))
      .toMatchObject({ text: "Done. The tests pass.", final: true });
    expect(events).toContainEqual(expect.objectContaining({ type: "narrating", utteranceId: "u1" }));
  });

  it("a new utterance cancels in-flight narration", async () => {
    let release: (value: string) => void = () => {};
    const { hooks, host, sessionId } = await startedTask(() => new Promise<string>(resolve => { release = resolve; }));
    hooks.chunk("Done.");
    hooks.finish();
    await flush();
    await host.request({ type: "interrupt", sessionId });
    release("Done. All good.");
    await flush();
    expect(mocks.speak).not.toHaveBeenCalled();
  });
});
