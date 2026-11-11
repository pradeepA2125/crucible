import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatMsg } from "../../types";
import { VoiceControl } from "../../components/voice/VoiceControl";
import { VoiceLiveLines } from "../../components/voice/VoiceLiveLines";
import { useVoiceSession } from "./useVoiceSession";
import type { VoiceBridge, VoiceCommand } from "./voiceBridge";
import { CAPTURE_FRAME_BYTES, type CaptureFrame } from "./speechSegments";
import { writeMicrophoneMode, writeSynthesizer } from "./voicePreferences";

vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const microphone = vi.hoisted(() => ({ onFrame: undefined as ((frame: CaptureFrame) => void) | undefined }));

vi.mock("./voiceAudio", () => ({
  VoiceAudio: class {
    playing = false;
    paused = false;
    play = vi.fn(); stopPlayback = vi.fn(); close = vi.fn(); closeMicrophone = vi.fn();
    setMicrophoneEnabled = vi.fn(); cue = vi.fn(() => 400); pausePlayback = vi.fn(); resumePlayback = vi.fn();
    outputLevel = vi.fn(() => 0);
    openMicrophone = vi.fn(async (options: { onFrame(frame: CaptureFrame): void }) => {
      microphone.onFrame = options.onFrame;
      return true;
    });
    resume = vi.fn(async () => {});
  },
}));

const msg = (id: string, role: "user" | "agent", content: string, metadata: Record<string, unknown>): ChatMsg =>
  ({ id, role, content, type: "text", timestamp: "2026-11-11T00:00:00Z", metadata });

function Host({ bridge, messages = [], threadId = "thread" }: { bridge: VoiceBridge; messages?: ChatMsg[]; threadId?: string }) {
  const voice = useVoiceSession({ bridge, threadId, identity: threadId, ready: true, messages });
  if (!voice) return null;
  return <>
    <VoiceControl voice={voice} />
    <div data-testid="lines"><VoiceLiveLines voice={voice} /></div>
  </>;
}

const READY = { available: true, installed: ["whisper-small"], tts: ["system"], pocketAvailable: true,
  pocketDownloadBytes: 199_269_026, pocketEngine: "onnx" };

function bridgeOf(handler: (command: VoiceCommand) => unknown): VoiceBridge {
  return { request: vi.fn(async (command: VoiceCommand) => handler(command)), subscribe: () => () => {} };
}
const sent = (bridge: VoiceBridge, type: VoiceCommand["type"]) =>
  (bridge.request as ReturnType<typeof vi.fn>).mock.calls.map(([command]) => command as VoiceCommand).filter(command => command.type === type);
const speak = (frames: number, rms: number) => {
  for (let n = 0; n < frames; n += 1) microphone.onFrame!({ pcm: new ArrayBuffer(CAPTURE_FRAME_BYTES), rms });
};

describe("useVoiceSession", () => {
  afterEach(() => { localStorage.clear(); microphone.onFrame = undefined; });

  it("offers Voice without repeating the conversation the chat already shows", async () => {
    const bridge = bridgeOf(() => READY);
    render(<Host bridge={bridge} messages={[
      msg("m1", "user", "how is it going", { voice: "exchange", voice_decision: "d1" }),
      msg("m2", "agent", "Step two of three.", { voice: "exchange", voice_decision: "d1" }),
    ]} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Voice" })).toBeEnabled());
    expect(screen.getByTestId("lines")).toBeEmptyDOMElement();
    expect(screen.queryByText("how is it going")).toBeNull();
  });

  it("disables Voice with the reason when local voice is unavailable here", async () => {
    const bridge = bridgeOf(() => ({ ...READY, available: false, installed: [], tts: [], pocketAvailable: false,
      reason: "Local voice needs a processor with AVX2 (most made since 2013)." }));
    render(<Host bridge={bridge} />);
    const button = screen.getByRole("button", { name: "Voice" });
    await waitFor(() => expect(button).toHaveAccessibleDescription("Local voice needs a processor with AVX2 (most made since 2013)."));
    expect(button).toBeDisabled();
  });

  it("first press downloads Whisper Small, starts with the system voice, then downloads Pocket TTS behind the session", async () => {
    const installs: { model: string; resolve(): void }[] = [];
    let installed: string[] = [];
    let tts: string[] = ["system"];
    const bridge = bridgeOf(command => {
      switch (command.type) {
        case "status": return { ...READY, installed, tts };
        case "install": return new Promise<void>(resolve => installs.push({ model: command.model, resolve: () => {
          if (command.model === "pocket") tts = ["system", "pocket"]; else installed = [command.model];
          resolve();
        } }));
        case "start": return { sessionId: "session" };
        default: return {};
      }
    });
    render(<Host bridge={bridge} />);
    fireEvent.click(await screen.findByRole("button", { name: "Voice" }));
    expect(await screen.findByText("Downloading Whisper Small…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(sent(bridge, "start")).toHaveLength(0);
    await act(async () => { installs[0].resolve(); });
    await waitFor(() => expect(sent(bridge, "start")).toHaveLength(1));
    expect(sent(bridge, "start")[0]).toMatchObject({ threadId: "thread", stt: "whisper-small", tts: "system" });
    expect(await screen.findByRole("button", { name: "End voice session" })).toBeInTheDocument();
    expect(await screen.findByText("Downloading Pocket TTS…")).toBeInTheDocument();
    expect(installs.map(install => install.model)).toEqual(["whisper-small", "pocket"]);
    await act(async () => { installs[1].resolve(); });
    await waitFor(() => expect(screen.queryByText("Downloading Pocket TTS…")).toBeNull());
    expect(screen.getByRole("button", { name: "End voice session" })).toBeInTheDocument();
  });

  it("an explicit Text only choice starts text-only and downloads nothing", async () => {
    writeSynthesizer("none");
    const bridge = bridgeOf(command => command.type === "start" ? { sessionId: "session" } : READY);
    render(<Host bridge={bridge} />);
    fireEvent.click(await screen.findByRole("button", { name: "Voice" }));
    await waitFor(() => expect(sent(bridge, "start")).toHaveLength(1));
    expect(sent(bridge, "start")[0]).toMatchObject({ tts: "none" });
    expect(sent(bridge, "install")).toHaveLength(0);
  });

  it("push to talk from Settings shows Hold to talk with its own End button, and the saved mode applies live", async () => {
    writeMicrophoneMode("push-to-talk");
    const bridge = bridgeOf(command => command.type === "start" ? { sessionId: "session" } : READY);
    render(<Host bridge={bridge} />);
    fireEvent.click(await screen.findByRole("button", { name: "Voice" }));
    expect(await screen.findByRole("button", { name: "Hold to talk, release to send" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "End voice session" })).toHaveTextContent("End voice");
    act(() => {
      localStorage.setItem("crucible.voice.microphone", "hands-free");
      window.dispatchEvent(new StorageEvent("storage", { key: "crucible.voice.microphone", newValue: "hands-free" }));
    });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Hold to talk, release to send" })).toBeNull());
    expect(screen.getByRole("button", { name: "End voice session" })).toHaveTextContent(/Listening|Opening microphone/);
    fireEvent.click(screen.getByRole("button", { name: "End voice session" }));
    expect(await screen.findByRole("button", { name: "Voice" })).toBeInTheDocument();
    expect(sent(bridge, "stop")).toHaveLength(1);
  });

  it("an error about an earlier spoken turn clears once a later one goes through, and the turn shows as spoken", async () => {
    let failing = true;
    const bridge = bridgeOf(command => {
      switch (command.type) {
        case "status": return READY;
        case "start": return { sessionId: "session" };
        case "decode": return { text: "Use SQLite for the tests" };
        case "decide": return failing ? { refused: "voice_agent_unavailable", message: "The model did not answer." }
          : { decisionId: "d2", turn: "complete", action: "reply", say: "Sure.", request: "" };
        case "commit": return { action: "reply" };
        default: return {};
      }
    });
    render(<Host bridge={bridge} />);
    fireEvent.click(await screen.findByRole("button", { name: "Voice" }));
    await waitFor(() => expect(microphone.onFrame).toBeDefined());
    speak(12, 0.03); speak(400, 0);
    expect((await screen.findByRole("alert")).textContent).toContain("did not answer");
    failing = false;
    speak(12, 0.03); speak(40, 0);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(await screen.findByText("Use SQLite for the tests")).toBeInTheDocument();
    expect(screen.getByText("Spoken")).toBeInTheDocument();
  });
});
