import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatMsg } from "../../types";
import { VoicePanel } from "./VoicePanel";
import type { VoiceBridge, VoiceCommand } from "./voiceBridge";
import { CAPTURE_FRAME_BYTES, type CaptureFrame } from "./speechSegments";

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

describe("VoicePanel", () => {
  it("does not repeat the voice conversation the chat already shows", async () => {
    const bridge: VoiceBridge = {
      request: vi.fn(async () => ({ available: true, installed: ["whisper-small"], tts: ["system"] })),
      subscribe: () => () => {},
    };
    render(<VoicePanel id="voice" bridge={bridge} threadId="thread" messages={[
      msg("m1", "user", "how is it going", { voice: "exchange", voice_decision: "d1" }),
      msg("m2", "agent", "Step two of three.", { voice: "exchange", voice_decision: "d1" }),
    ]} />);
    await waitFor(() => expect(bridge.request).toHaveBeenCalled());
    expect(await screen.findByText("Start voice session")).toBeTruthy();
    expect(screen.queryByText("how is it going")).toBeNull();
    expect(screen.queryByText("Step two of three.")).toBeNull();
  });

  it("an error about an earlier spoken turn clears once a later one goes through", async () => {
    let failing = true;
    const bridge: VoiceBridge = {
      request: vi.fn(async (command: VoiceCommand) => {
        switch (command.type) {
          case "status": return { available: true, installed: ["whisper-small"], tts: ["system"] };
          case "start": return { sessionId: "session" };
          case "decode": return { text: "Use SQLite for the tests" };
          case "decide": return failing ? { refused: "voice_agent_unavailable", message: "The model did not answer." }
            : { decisionId: "d2", turn: "complete", action: "reply", say: "Sure.", request: "" };
          case "commit": return { action: "reply" };
          default: return {};
        }
      }),
      subscribe: () => () => {},
    };
    render(<VoicePanel id="voice" bridge={bridge} threadId="thread" messages={[]} />);
    fireEvent.click(await screen.findByText("Start voice session"));
    await waitFor(() => expect(microphone.onFrame).toBeDefined());
    const speak = (frames: number, rms: number) => {
      for (let n = 0; n < frames; n += 1) microphone.onFrame!({ pcm: new ArrayBuffer(CAPTURE_FRAME_BYTES), rms });
    };
    speak(12, 0.03); speak(400, 0);
    expect((await screen.findByRole("alert")).textContent).toContain("did not answer");
    failing = false;
    speak(12, 0.03); speak(40, 0);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  function statusBridge(status: Record<string, unknown>): VoiceBridge {
    return { request: vi.fn(async () => status), subscribe: () => () => {} };
  }

  it("offers text-only replies and starts without a voice", async () => {
    render(<VoicePanel id="voice" bridge={statusBridge({ available: true, installed: ["whisper-small"], tts: [],
      pocketAvailable: true, pocketDownloadBytes: 199_269_026, pocketEngine: "onnx" })} threadId="thread" messages={[]} />);
    const replies = await screen.findByLabelText("Spoken replies") as HTMLSelectElement;
    expect(replies.value).toBe("none");
    expect(replies.selectedOptions[0]?.textContent).toBe("Text only");
    expect((screen.getByText("Start voice session") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText("Install spoken replies (about 200 MB)")).toBeTruthy();
  });

  it("shows the processor reason and disables start", async () => {
    render(<VoicePanel id="voice" bridge={statusBridge({ available: false, installed: [], tts: [], pocketAvailable: false,
      pocketDownloadBytes: 0, pocketEngine: "onnx",
      reason: "Local voice needs a processor with AVX2 (most made since 2013)." })} threadId="thread" messages={[]} />);
    expect(await screen.findByText("Local voice needs a processor with AVX2 (most made since 2013).")).toBeTruthy();
    expect(screen.queryByText("Start voice session")).toBeNull();
  });

  it("Apple Silicon keeps Install Pocket TTS", async () => {
    render(<VoicePanel id="voice" bridge={statusBridge({ available: true, installed: ["whisper-small"], tts: [],
      pocketAvailable: true, pocketDownloadBytes: 528_850_937, pocketEngine: "coreml" })} threadId="thread" messages={[]} />);
    expect(await screen.findByText("Install Pocket TTS")).toBeTruthy();
    expect(screen.queryByText(/Install spoken replies/)).toBeNull();
  });
});
