import { describe, it, expect } from "vitest";
import { detectTrigger } from "./composerTrigger";

describe("detectTrigger — slash", () => {
  it("detects a slash command being typed at the very start", () => {
    expect(detectTrigger("/rev", 4)).toEqual({ kind: "slash", query: "rev", start: 0, end: 4 });
  });

  it("does not trigger once a space has been typed after the name", () => {
    expect(detectTrigger("/review src/a.py", 8)).toBeNull();
  });

  it("does not trigger for a slash that isn't at the start of the message", () => {
    expect(detectTrigger("hello /world", 12)).toBeNull();
  });

  it("returns an empty query for a bare slash", () => {
    expect(detectTrigger("/", 1)).toEqual({ kind: "slash", query: "", start: 0, end: 1 });
  });
});

describe("detectTrigger — file mention", () => {
  it("detects an @-mention anywhere in the text", () => {
    expect(detectTrigger("look at @src/fo", 15)).toEqual({
      kind: "file", query: "src/fo", start: 8, end: 15,
    });
  });

  it("does not trigger once whitespace follows the mention", () => {
    const text = "look at @src/foo.py more";
    expect(detectTrigger(text, text.length)).toBeNull();
  });

  it("returns an empty query for a bare @", () => {
    expect(detectTrigger("hi @", 4)).toEqual({ kind: "file", query: "", start: 3, end: 4 });
  });

  it("returns null with no trigger character before the cursor", () => {
    expect(detectTrigger("just plain text", 6)).toBeNull();
  });
});
