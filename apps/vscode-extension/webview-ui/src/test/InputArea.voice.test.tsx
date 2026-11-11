import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InputArea } from "../components/InputArea";
import { VoiceContext, type VoiceComposerModel } from "../components/voice/VoiceContext";
import { INITIAL_VOICE_STATE } from "../desktop/voice/voiceSession";
import type { InputAvailability } from "../inputAvailability";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

let postMessage: ReturnType<typeof vi.fn>;
beforeEach(async () => {
  postMessage = (await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

const IDLE: InputAvailability = { disabled: false, placeholder: "Message", showStop: false, taskStop: false };
const RUNNING: InputAvailability = { disabled: true, placeholder: "Agent is working…", showStop: true, taskStop: false };

function voiceModel(overrides: Partial<VoiceComposerModel> = {}): VoiceComposerModel {
  return {
    status: { available: true, installed: ["whisper-small"], tts: ["system"], pocketAvailable: true, pocketDownloadBytes: 1, pocketEngine: "onnx" },
    active: false, starting: false, state: INITIAL_VOICE_STATE, pushToTalk: false, turns: [], reviews: [], download: undefined, error: "",
    start: vi.fn(), end: vi.fn(), toggleMicrophone: vi.fn(), stopSpeaking: vi.fn(), sendNow: vi.fn(), beginHold: vi.fn(),
    cancelHold: vi.fn(), cancelDownload: vi.fn(), discardReview: vi.fn(), submitText: vi.fn(async () => {}), ...overrides,
  };
}

function Composer({ voice, availability = IDLE, initial = "" }: { voice?: VoiceComposerModel; availability?: InputAvailability; initial?: string }) {
  const [draft, setDraft] = useState(initial);
  const area = <InputArea availability={availability} draft={draft} onDraftChange={setDraft} threadId="thread" threadUsage={null} teamNames={{}} />;
  return voice ? <VoiceContext.Provider value={voice}>{area}</VoiceContext.Provider> : area;
}

describe("InputArea with a voice session", () => {
  it("shows no voice control without a desktop voice bridge", () => {
    render(<Composer />);
    expect(screen.queryByRole("button", { name: "Voice" })).toBeNull();
  });

  it("starts a session from the composer's Voice button", () => {
    const voice = voiceModel();
    render(<Composer voice={voice} />);
    fireEvent.click(screen.getByRole("button", { name: "Voice" }));
    expect(voice.start).toHaveBeenCalledOnce();
  });

  it("sends typed text to the running session, even while the turn is running, and clears the draft", () => {
    const voice = voiceModel({ active: true, state: { ...INITIAL_VOICE_STATE, connected: true, microphone: "on" } });
    render(<Composer voice={voice} availability={RUNNING} />);
    const input = screen.getByRole("textbox", { name: "Chat input" });
    expect(input).toBeEnabled();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "use sqlite instead" } });
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeEnabled();
    fireEvent.click(send);
    expect(voice.submitText).toHaveBeenCalledExactlyOnceWith("use sqlite instead");
    expect(postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "sendMessage" }));
    expect(input).toHaveValue("");
  });

  it("Enter sends to the session too, and a slash command keeps the normal path", () => {
    const voice = voiceModel({ active: true, state: { ...INITIAL_VOICE_STATE, connected: true, microphone: "on" } });
    render(<Composer voice={voice} />);
    const input = screen.getByRole("textbox", { name: "Chat input" });
    fireEvent.change(input, { target: { value: "what changed?" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(voice.submitText).toHaveBeenCalledExactlyOnceWith("what changed?");
    fireEvent.change(input, { target: { value: "/review now" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(voice.submitText).toHaveBeenCalledOnce();
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "expandPrompt", name: "review" }));
  });

  it("takes a review-held transcript as an editable draft after a blank line, then discards it", async () => {
    const voice = voiceModel({ active: true, state: { ...INITIAL_VOICE_STATE, connected: true, microphone: "on" },
      reviews: [{ id: "r1", transcript: "use sqlite for the tests" }] });
    render(<Composer voice={voice} initial="Also:" />);
    const input = screen.getByRole("textbox", { name: "Chat input" });
    await act(async () => {});
    expect(input).toHaveValue("Also:\n\nuse sqlite for the tests");
    expect(voice.discardReview).toHaveBeenCalledExactlyOnceWith("r1");
  });
});
