import { describe, expect, it } from "vitest";
import { parseEtime, parsePsLine } from "../src/runtime/process-info.js";

describe("ps parsing", () => {
  it.each([["05", null], ["01:05", 65], ["02:01:05", 7265], ["3-02:01:05", 266465]])(
    "etime %s", (raw, sec) => expect(parseEtime(raw)).toBe(sec));
  it("parses uid, etime and a command with spaces", () => {
    expect(parsePsLine("  501   01:05 /v/python -m agentd.serve --port 0 --workspace-lock /a/proj 2", 1000))
      .toEqual({ uid: 501, startedAtSec: 935,
        command: "/v/python -m agentd.serve --port 0 --workspace-lock /a/proj 2" });
    expect(parsePsLine("garbage", 1000)).toBeNull();
  });
});
