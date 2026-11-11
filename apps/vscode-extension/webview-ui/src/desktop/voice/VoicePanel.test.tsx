import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatMsg } from "../../types";
import { VoicePanel } from "./VoicePanel";
import type { VoiceBridge } from "./voiceBridge";

const msg = (id: string, role: "user" | "agent", content: string, metadata: Record<string, unknown>): ChatMsg =>
  ({ id, role, content, type: "text", timestamp: "2026-11-11T00:00:00Z", metadata });

describe("VoicePanel", () => {
  it("shows the saved voice conversation, so it survives a restart", async () => {
    const bridge: VoiceBridge = {
      request: vi.fn(async () => ({ available: true, installed: ["whisper-small"], tts: ["system"] })),
      subscribe: () => () => {},
    };
    render(<VoicePanel id="voice" bridge={bridge} threadId="thread" messages={[
      msg("m1", "user", "how is it going", { voice: "exchange", voice_decision: "d1" }),
      msg("m2", "agent", "Step two of three.", { voice: "exchange", voice_decision: "d1" }),
      msg("m3", "user", "add a readme", {}),
    ]} />);
    const transcript = await screen.findByLabelText("Voice transcript");
    expect(transcript.textContent).toContain("how is it going");
    expect(transcript.textContent).toContain("Step two of three.");
    expect(transcript.textContent).not.toContain("add a readme");
    await waitFor(() => expect(bridge.request).toHaveBeenCalled());
  });
});
