import { describe, expect, it } from "vitest";
import { parseSlashCommand, resolveSkillCommand, buildSlashDropdownItems } from "./slash";

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

describe("buildSlashDropdownItems", () => {
  it("badges prompts and skills, filtered by query", () => {
    const items = buildSlashDropdownItems(
      "rev",
      ["review", "changelog"],
      [{ name: "git-commit", description: "Commit staged changes" }],
    );
    expect(items).toEqual([{ id: "review", label: "review", badge: "Prompt" }]);
  });

  it("prompt wins on name collision with a skill", () => {
    const items = buildSlashDropdownItems(
      "",
      ["shared-name"],
      [{ name: "shared-name", description: "a skill" }],
    );
    expect(items).toEqual([{ id: "shared-name", label: "shared-name", badge: "Prompt" }]);
  });
});
