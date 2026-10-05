import { describe, expect, it } from "vitest";
import { parseHandshake, StdoutLines } from "../src/runtime/backend-process.js";

describe("StdoutLines", () => {
  it("joins a line split across chunks and buffers until subscribed", () => {
    const lines = new StdoutLines();
    lines.push("INFO: hello\nCRUCIBLE_SER");
    lines.push('VE {"pid": 5, "port": 9}\nmore');
    const seen: string[] = [];
    lines.subscribe((l) => seen.push(l));
    expect(seen).toEqual(["INFO: hello", 'CRUCIBLE_SERVE {"pid": 5, "port": 9}']);
    lines.push(" output\n");
    expect(seen.at(-1)).toBe("more output");
  });
});

describe("parseHandshake", () => {
  it("parses the line and rejects anything else", () => {
    expect(parseHandshake('CRUCIBLE_SERVE {"pid": 5, "port": 9}')).toEqual({ pid: 5, port: 9 });
    expect(parseHandshake('CRUCIBLE_SERVE {"pid": "5", "port": 9}')).toBeNull();
    expect(parseHandshake("INFO: Uvicorn running")).toBeNull();
  });
});
