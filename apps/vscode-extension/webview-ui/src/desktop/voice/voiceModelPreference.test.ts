import { beforeEach, describe, expect, it } from "vitest";
import { readVoiceModel, voiceModelChoices, writeVoiceModel } from "./voiceModelPreference";

describe("voice model preference", () => {
  beforeEach(() => localStorage.clear());

  it("is Automatic (null) until chosen", () => {
    expect(readVoiceModel("chatgpt")).toBeNull();
  });

  it("is kept per provider", () => {
    writeVoiceModel("chatgpt", "gpt-6-luna");
    expect(readVoiceModel("chatgpt")).toBe("gpt-6-luna");
    expect(readVoiceModel("claude_subscription")).toBeNull();
  });

  it("null goes back to Automatic", () => {
    writeVoiceModel("chatgpt", "gpt-6-luna");
    writeVoiceModel("chatgpt", null);
    expect(readVoiceModel("chatgpt")).toBeNull();
    expect(localStorage.getItem("crucible.voiceModel.chatgpt")).toBeNull();
  });

  it("ignores a stored value that is not a model id", () => {
    localStorage.setItem("crucible.voiceModel.chatgpt", "bad value; drop");
    expect(readVoiceModel("chatgpt")).toBeNull();
  });

  it("offers Automatic plus the current provider's models only", () => {
    const options = [
      { backend: "chatgpt", label: "ChatGPT plan", model: "gpt-6.1-sol", active: true },
      { backend: "chatgpt", label: "ChatGPT plan", model: "gpt-6-luna", display: "GPT-6 Luna", active: false },
      { backend: "claude_subscription", label: "Claude plan", model: "claude-haiku-5-5", active: false },
    ];
    expect(voiceModelChoices("chatgpt", options, null)).toEqual([
      { value: "", label: "Automatic" },
      { value: "gpt-6.1-sol", label: "gpt-6.1-sol" },
      { value: "gpt-6-luna", label: "GPT-6 Luna" },
    ]);
  });

  it("offers Automatic only for a provider with no catalog", () => {
    expect(voiceModelChoices("ollama", [], null)).toEqual([{ value: "", label: "Automatic" }]);
  });

  it("keeps a saved choice the catalog no longer lists", () => {
    expect(voiceModelChoices("chatgpt", [], "gpt-6-luna")).toEqual([
      { value: "", label: "Automatic" }, { value: "gpt-6-luna", label: "gpt-6-luna" },
    ]);
  });
});
