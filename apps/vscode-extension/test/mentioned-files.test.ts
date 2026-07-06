import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { readMentionedFiles, MENTION_FILE_MAX_CHARS } from "../src/mentioned-files.js";

describe("readMentionedFiles", () => {
  it("reads and returns file content for each path", () => {
    const ws = fs.mkdtempSync(path.join(os.tmpdir(), "mention-"));
    fs.writeFileSync(path.join(ws, "a.txt"), "hello world");
    const result = readMentionedFiles(ws, ["a.txt"]);
    expect(result).toEqual([{ path: "a.txt", content: "hello world" }]);
  });

  it("caps content at MENTION_FILE_MAX_CHARS", () => {
    const ws = fs.mkdtempSync(path.join(os.tmpdir(), "mention-"));
    const big = "x".repeat(MENTION_FILE_MAX_CHARS + 500);
    fs.writeFileSync(path.join(ws, "big.txt"), big);
    const result = readMentionedFiles(ws, ["big.txt"]);
    expect(result[0].content.length).toBeLessThanOrEqual(MENTION_FILE_MAX_CHARS + 50); // + truncation marker
    expect(result[0].content.startsWith("x".repeat(100))).toBe(true);
  });

  it("marks a missing file as unreadable instead of throwing", () => {
    const ws = fs.mkdtempSync(path.join(os.tmpdir(), "mention-"));
    const result = readMentionedFiles(ws, ["does-not-exist.txt"]);
    expect(result).toEqual([{ path: "does-not-exist.txt", content: "(file not found or unreadable)" }]);
  });
});
