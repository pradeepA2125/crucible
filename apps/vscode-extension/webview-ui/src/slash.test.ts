import { describe, expect, it } from "vitest";
import { parseSlashCommand, resolveSkillCommand } from "./slash";

describe("parseSlashCommand", () => {
  it("parses name and args", () => {
    expect(parseSlashCommand("/review src/foo.py")).toEqual({ name: "review", args: "src/foo.py" });
  });
  it("returns null for non-commands", () => {
    expect(parseSlashCommand("hello")).toBeNull();
  });
});

describe("resolveSkillCommand", () => {
  it("forced-loads a known skill, sending the args as the message", () => {
    expect(resolveSkillCommand("git-commit", "stage and commit", ["git-commit"])).toEqual({
      forcedSkills: ["git-commit"],
      message: "stage and commit",
    });
  });

  it("returns null when the name is not a skill (falls back to plain send)", () => {
    expect(resolveSkillCommand("unknown", "x", ["git-commit"])).toBeNull();
  });

  it("empty args still activates the skill with an empty message", () => {
    expect(resolveSkillCommand("git-commit", "", ["git-commit"])).toEqual({
      forcedSkills: ["git-commit"],
      message: "",
    });
  });
});
