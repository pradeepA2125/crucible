import { describe, expect, it } from "vitest";
import { addsWords } from "./speechText";

describe("addsWords: did the speaker say something new?", () => {
  it.each([
    ["Use SQLite for the tests", "Use SQLite for the tests and Postgres", true],
    ["Use SQLite for the tests", "Use SQLite for the tests. Wait", true],
    ["today won't understand the code", "today I don't understand the code", false],
    ["Use SQLite for the tests", "Use SQLite for the tests.", false],
    ["Use SQLite for the tests", "Use SQLite for the tests (cars honking) [wind blowing]", false],
    ["Use SQLite for the tests", "Use SQLite for the tests um hmm", false],
    ["Use SQLite for the tests", "Use SQLite for the", false],
    ["", "Hello there", true],
  ])("%j then %j → %s", (before, after, expected) => {
    expect(addsWords(before, after)).toBe(expected);
  });
});
