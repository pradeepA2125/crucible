import { describe, expect, it } from "vitest";
import type { ChatMsg } from "../../types";
import { unsettledVoiceTurns, voiceTurns } from "./voiceTurns";

const msg = (id: string, role: "user" | "agent", content: string, metadata: Record<string, unknown> = {}): ChatMsg =>
  ({ id, role, content, type: "text", timestamp: "2026-11-11T00:00:00Z", metadata });
const exchange = (decision: string) => ({ voice: "exchange", voice_decision: decision });

describe("voiceTurns", () => {
  it("pairs heard and said by decision", () => {
    expect(voiceTurns([
      msg("m1", "user", "how is it going", exchange("d1")),
      msg("m2", "agent", "Step two of three.", exchange("d1")),
    ])).toEqual([{ id: "d1", decisionId: "d1", text: "how is it going", reply: "Step two of three.", final: true }]);
  });

  it("attaches narration to the spoken request its answer answers", () => {
    expect(voiceTurns([
      msg("u1", "user", "fix the tests", { voice: true }),
      msg("a1", "agent", "## Fixed\n- Tests pass."),
      msg("n1", "agent", "Fixed, and the tests pass.", { voice: "narration", answer_id: "a1" }),
    ])).toEqual([{ id: "u1", text: "fix the tests", reply: "Fixed, and the tests pass.", final: true }]);
  });

  it("ignores typed messages and written answers", () => {
    expect(voiceTurns([msg("u1", "user", "add a readme"), msg("a1", "agent", "Added.")])).toEqual([]);
  });

  it("keeps the newest 30 turns", () => {
    const many = Array.from({ length: 40 }, (_, n) => msg(`m${n}`, "user", `q${n}`, exchange(`d${n}`)));
    const turns = voiceTurns(many);
    expect(turns).toHaveLength(30);
    expect(turns[0]?.text).toBe("q10");
  });
});

describe("unsettledVoiceTurns: what the panel shows that the chat cannot", () => {
  const receipt = (status: "queued" | "incorporated" | "expired") =>
    ({ message_id: "live1", turn_id: "turn-a", kind: "steer" as const, text: "use sqlite", status });
  const saved = [{ id: "d1", decisionId: "d1", text: "use sqlite", reply: "On it.", final: true }];

  it("a turn already saved in the chat leaves the panel", () => {
    expect(unsettledVoiceTurns(saved, [{ id: "live1", decisionId: "d1", text: "use sqlite", reply: "On it.", final: true }]))
      .toEqual([]);
  });

  it("a turn still in flight stays", () => {
    const live = [{ id: "live2", text: "and also", final: false }];
    expect(unsettledVoiceTurns(saved, live)).toEqual(live);
  });

  it("a correction stays while it is queued or after it expired, and leaves once it reached the model", () => {
    const turn = (status: "queued" | "incorporated" | "expired") =>
      [{ id: "live1", decisionId: "d1", text: "use sqlite", reply: "On it.", final: true, receipt: receipt(status) }];
    expect(unsettledVoiceTurns(saved, turn("queued"))).toHaveLength(1);
    expect(unsettledVoiceTurns(saved, turn("expired"))).toHaveLength(1);
    expect(unsettledVoiceTurns(saved, turn("incorporated"))).toEqual([]);
  });

  it("a spoken request that started work is matched by its words", () => {
    expect(unsettledVoiceTurns([{ id: "u1", text: "fix the tests", final: true }],
      [{ id: "live1", decisionId: "d9", text: "Fix the  tests", reply: "On it.", final: true }])).toEqual([]);
  });
});
