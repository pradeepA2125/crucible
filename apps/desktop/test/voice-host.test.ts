import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceHost, type VoiceSnapshot, type VoiceThread } from "../src/voice/host.js";

const mocks = vi.hoisted(() => ({
  speak: vi.fn<(text: string, audio: (pcm: ArrayBuffer, sampleRate: number) => void, signal: AbortSignal) => Promise<void>>(),
}));

vi.mock("../src/voice/assets.js", () => ({
  VoiceAssets: class {
    async installed() { return ["whisper-small"]; }
    async pocketInstalled() { return false; }
    async verifiedPath() { return "/verified/model.bin"; }
    async cancelAndWait() {}
  },
}));
vi.mock("../src/voice/workers.js", () => ({
  Recognizer: class {
    async wait() {}
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
  await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true })));
});

async function fixture(first: VoiceSnapshot) {
  const path = await mkdtemp(join(tmpdir(), "crucible-voice-host-"));
  directories.push(path);
  await Promise.all(["crucible-whisper", "crucible-tts"].map(file => writeFile(join(path, file), "", { mode: 0o700 })));
  let snapshot = first;
  const sendIdle = vi.fn<VoiceThread["sendIdle"]>().mockResolvedValue(undefined);
  const submit = vi.fn<VoiceThread["submit"]>().mockImplementation(async request => ({
    message_id: request.message_id, turn_id: request.expected_turn_id, kind: "reply",
    status: "answered", text: "Still working.",
  }));
  const thread: VoiceThread = {
    current: () => true, snapshot: async () => snapshot, submit, sendIdle, receipt: async () => null,
  };
  const events: Record<string, unknown>[] = [];
  const host = new VoiceHost({
    profile: path, helpers: path, platform: "darwin", architecture: "arm64",
    thread: async () => thread, publish: event => events.push(event),
  });
  sessions.push(host);
  const { sessionId } = await host.request({ type: "start", threadId: "thread",
    stt: "whisper-small", tts: "system" }) as { sessionId: string };
  return { host, sessionId, submit, sendIdle, events, setSnapshot: (value: VoiceSnapshot) => { snapshot = value; } };
}

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
