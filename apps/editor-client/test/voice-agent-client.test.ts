import { describe, expect, it } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client";

type Seen = { url: string; body: Record<string, unknown> };

function client(payload: unknown, status = 200, seen: Seen[] = []) {
  return new HttpBackendClient({
    baseUrl: "http://test", fetchFn: async (url, init) => {
      seen.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : {} });
      return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
    },
  });
}

describe("voice agent client", () => {
  it("maps a decision and sends snake_case", async () => {
    const seen: Seen[] = [];
    const decision = await client({
      decision_id: "d1", turn: "complete", action: "start_task", say: "On it.", request: "Add a README",
      turn_id: null,
    }, 200, seen).voiceDecide("t 1", { utterance: "add a readme", silenceMs: 700, finalCall: false, expectedProcessId: "p" });
    expect(decision).toEqual({
      decisionId: "d1", turn: "complete", action: "start_task", say: "On it.", request: "Add a README", turnId: null,
    });
    expect(seen[0].url).toBe("http://test/v1/chat/threads/t%201/voice/decide");
    expect(seen[0].body).toEqual({ utterance: "add a readme", silence_ms: 700, final_call: false, expected_process_id: "p" });
  });

  it("maps each commit result", async () => {
    const seen: Seen[] = [];
    expect(await client({ action: "reply" }, 200, seen).voiceCommit("t", "d1", "p")).toEqual({ action: "reply" });
    expect(seen[0].body).toEqual({ decision_id: "d1", expected_process_id: "p" });
    expect(await client({ action: "start_task", message: "add a readme", voice_request: "Add a README" })
      .voiceCommit("t", "d1", "p")).toEqual({ action: "start_task", message: "add a readme", voiceRequest: "Add a README" });
    const receipt = { message_id: "d1", turn_id: "turn-a", kind: "steer", text: "Got it.", status: "queued" };
    expect(await client({ action: "steer", receipt }).voiceCommit("t", "d1", "p")).toEqual({ action: "steer", receipt });
  });

  it("surfaces turn_active with its status and code", async () => {
    const refused = client({ detail: { code: "turn_active", active_turn_id: "turn-b" } }, 409).voiceCommit("t", "d1", "p");
    await expect(refused).rejects.toMatchObject({ status: 409, detail: { code: "turn_active" } });
  });

  it("sends voice_model only when a model is chosen", async () => {
    const seen: Seen[] = [];
    const decision = { decision_id: "d1", turn: "complete", action: "reply", say: "Hi.", request: "", turn_id: null };
    await client(decision, 200, seen).voiceDecide("t", {
      utterance: "hi", silenceMs: 700, finalCall: false, expectedProcessId: "p", voiceModel: "gpt-6-luna" });
    await client(decision, 200, seen).voiceDecide("t", { utterance: "hi", silenceMs: 700, finalCall: false, expectedProcessId: "p" });
    await client({ say: "Done." }, 200, seen).voiceNarrate("t", {
      heard: "fix it", answer: "Fixed.", expectedProcessId: "p", voiceModel: "gpt-6-luna" });
    expect(seen[0].body.voice_model).toBe("gpt-6-luna");
    expect("voice_model" in seen[1].body).toBe(false);
    expect(seen[2].body.voice_model).toBe("gpt-6-luna");
  });

  it("reads the project vocabulary", async () => {
    const seen: Seen[] = [];
    expect(await client({ words: ["crucible", "ControllerLoop"] }, 200, seen).voiceVocabulary())
      .toEqual(["crucible", "ControllerLoop"]);
    expect(seen[0].url).toBe("http://test/v1/voice/vocabulary");
    await expect(client({ words: "nope" }).voiceVocabulary()).rejects.toThrow();
  });

  it("narrates", async () => {
    const seen: Seen[] = [];
    expect(await client({ say: "Done." }, 200, seen).voiceNarrate("t", { heard: "fix it", answer: "Fixed.", expectedProcessId: "p" }))
      .toBe("Done.");
    expect(seen[0].body).toEqual({ heard: "fix it", answer: "Fixed.", expected_process_id: "p" });
  });

  it("sends voice_request only when set", async () => {
    const seen: Seen[] = [];
    const ok = { done: true };
    await client(ok, 200, seen).sendChatMessage("t", "add a readme", undefined, { voiceRequest: "Add a README" }).catch(() => {});
    await client(ok, 200, seen).sendChatMessage("t", "add a readme").catch(() => {});
    expect(seen[0].body).toMatchObject({ content: "add a readme", voice_request: "Add a README" });
    expect(seen[1].body).not.toHaveProperty("voice_request");
  });
});
