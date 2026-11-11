import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { INITIAL_VOICE_STATE } from "../../desktop/voice/voiceSession";
import { VoiceLiveLines } from "./VoiceLiveLines";
import type { VoiceComposerModel } from "./VoiceContext";

vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

function model(overrides: Partial<VoiceComposerModel> = {}): VoiceComposerModel {
  return {
    status: undefined, active: true, starting: false, state: INITIAL_VOICE_STATE, pushToTalk: false, turns: [], reviews: [],
    download: undefined, error: "", start: vi.fn(), end: vi.fn(), toggleMicrophone: vi.fn(), stopSpeaking: vi.fn(),
    sendNow: vi.fn(), beginHold: vi.fn(), cancelHold: vi.fn(), cancelDownload: vi.fn(), discardReview: vi.fn(),
    submitText: vi.fn(async () => {}), ...overrides,
  };
}

describe("VoiceLiveLines", () => {
  it("renders nothing with nothing in flight", () => {
    const { container } = render(<VoiceLiveLines voice={model()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the words heard so far as the user's in-progress message with Send now while hands-free", () => {
    const voice = model({ state: { ...INITIAL_VOICE_STATE, connected: true, microphone: "on", capturing: true, partial: "use sqlite for" } });
    render(<VoiceLiveLines voice={voice} />);
    expect(screen.getByTestId("voice-hearing")).toHaveTextContent("Hearing");
    expect(screen.getByTestId("user-bubble")).toHaveTextContent("use sqlite for");
    fireEvent.click(screen.getByRole("button", { name: "Send now" }));
    expect(voice.sendNow).toHaveBeenCalledOnce();
  });

  it("labels the heard words by phase and offers no Send now in push to talk or once deciding", () => {
    const { rerender } = render(<VoiceLiveLines voice={model({ pushToTalk: true,
      state: { ...INITIAL_VOICE_STATE, connected: true, microphone: "on", capturing: true, partial: "use sqlite" } })} />);
    expect(screen.queryByRole("button", { name: "Send now" })).toBeNull();
    rerender(<VoiceLiveLines voice={model({ state: { ...INITIAL_VOICE_STATE, connected: true, microphone: "on", deciding: true, partial: "use sqlite" } })} />);
    expect(screen.getByTestId("voice-hearing")).toHaveTextContent("Thinking");
    expect(screen.queryByRole("button", { name: "Send now" })).toBeNull();
  });

  it("shows an in-flight turn as spoken with its reply, a typed one without the tag, and receipts as notes", () => {
    render(<VoiceLiveLines voice={model({ turns: [
      { id: "t1", text: "how is it going", reply: "Step two of", final: false },
      { id: "t2", text: "use sqlite", typed: true, reply: "Switching.", final: true,
        receipt: { message_id: "t2", turn_id: "turn", kind: "steer", text: "Switching.", status: "queued" } },
      { id: "t3", text: "never mind", typed: true,
        receipt: { message_id: "t3", turn_id: "turn", kind: "steer", text: "", status: "expired" } },
    ] })} />);
    expect(screen.getAllByText("Spoken")).toHaveLength(1);
    expect(screen.getByText("Speaking")).toBeInTheDocument();
    expect(screen.getByText("Spoken reply")).toBeInTheDocument();
    expect(screen.getByText("Correction queued")).toBeInTheDocument();
    expect(screen.getByText("Correction expired")).toBeInTheDocument();
  });

  it("shows the session's error as an alert", () => {
    render(<VoiceLiveLines voice={model({ error: "The microphone disconnected. Turn it on to try again." })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The microphone disconnected.");
  });
});
