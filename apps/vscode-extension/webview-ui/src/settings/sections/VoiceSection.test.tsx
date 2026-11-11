import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceSection } from "./VoiceSection";
import type { VoiceBridge, VoiceCommand } from "../../desktop/voice/voiceBridge";


const STATUS = { available: true, installed: ["whisper-small"], tts: ["system"], pocketAvailable: true,
  pocketDownloadBytes: 199_269_026, pocketEngine: "onnx" };

function bridgeOf(status: Record<string, unknown> = STATUS): VoiceBridge {
  return { request: vi.fn(async (command: VoiceCommand) => command.type === "status" ? status : {}), subscribe: () => () => {} };
}
const deliver = (data: unknown) => act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });

describe("Settings › Voice", () => {
  const send = vi.fn();
  beforeEach(() => send.mockClear());
  afterEach(() => { localStorage.clear(); delete window.crucibleVoice; });

  it("lists the models with Pocket's size from this computer, named Pocket TTS", async () => {
    window.crucibleVoice = bridgeOf();
    render(<VoiceSection send={send} />);
    expect(await screen.findByText("Pocket TTS")).toBeInTheDocument();
    expect(screen.getByText(/190 MiB/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download Pocket TTS" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Whisper Small" })).toBeInTheDocument();
  });

  it("offers the preferences and saves them for the conversation window", async () => {
    window.crucibleVoice = bridgeOf();
    render(<VoiceSection send={send} />);
    const replies = await screen.findByLabelText("Spoken replies") as HTMLSelectElement;
    // Pocket by default where it runs, even before it is downloaded.
    expect(replies.value).toBe("pocket");
    expect(Array.from(replies.options).map(option => option.textContent)).toEqual([
      "Pocket TTS · download required", "macOS system voice", "Text only",
    ]);
    fireEvent.change(replies, { target: { value: "none" } });
    expect(localStorage.getItem("crucible.voice.synthesizer")).toBe("none");
    const microphone = screen.getByLabelText("Microphone mode") as HTMLSelectElement;
    expect(microphone.value).toBe("hands-free");
    fireEvent.change(microphone, { target: { value: "push-to-talk" } });
    expect(localStorage.getItem("crucible.voice.microphone")).toBe("push-to-talk");
    const recognition = screen.getByLabelText("Speech recognition") as HTMLSelectElement;
    expect(recognition.value).toBe("whisper-small");
    expect(Array.from(recognition.options).map(option => option.textContent)).toEqual(["Whisper Small", "Whisper Base · download required"]);
    fireEvent.change(recognition, { target: { value: "whisper-base" } });
    expect(localStorage.getItem("crucible.voice.recognizer")).toBe("whisper-base");
  });

  it("picks the voice model per provider from the composer's catalog", async () => {
    window.crucibleVoice = bridgeOf();
    render(<VoiceSection send={send} />);
    await screen.findByLabelText("Spoken replies");
    expect(send).toHaveBeenCalledWith({ type: "settings/listVoiceModels" });
    const model = screen.getByLabelText("Voice model") as HTMLSelectElement;
    expect(model).toBeDisabled();
    deliver({ type: "settings/voiceModels", backend: "chatgpt", options: [
      { backend: "chatgpt", model: "gpt-6", display: "GPT-6" }, { backend: "anthropic", model: "claude-haiku-5-5" },
    ] });
    expect(Array.from(model.options).map(option => option.textContent)).toEqual(["Automatic", "GPT-6"]);
    fireEvent.change(model, { target: { value: "gpt-6" } });
    expect(localStorage.getItem("crucible.voiceModel.chatgpt")).toBe("gpt-6");
  });

  it("hides the choices that this machine cannot make", async () => {
    window.crucibleVoice = bridgeOf({ ...STATUS, tts: [], pocketAvailable: false });
    render(<VoiceSection send={send} />);
    const replies = await screen.findByLabelText("Spoken replies") as HTMLSelectElement;
    expect(Array.from(replies.options).map(option => option.textContent)).toEqual(["Text only"]);
    expect(screen.queryByText("Pocket TTS")).toBeNull();
  });

  it("shows the reason when local voice is unavailable", async () => {
    window.crucibleVoice = bridgeOf({ available: false, installed: [], tts: [], pocketAvailable: false,
      reason: "Local voice needs a processor with AVX2 (most made since 2013)." });
    render(<VoiceSection send={send} />);
    expect(await screen.findByText("Local voice needs a processor with AVX2 (most made since 2013).")).toBeInTheDocument();
    expect(screen.queryByLabelText("Spoken replies")).toBeNull();
  });
});
