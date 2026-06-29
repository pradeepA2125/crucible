import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { promises as fsp } from "fs";
import * as os from "os";
import * as path from "path";
import {
  substitutePrompt,
  parseSlashCommand,
  listPromptNames,
  loadPromptBody,
} from "../src/prompt-files.js";

describe("substitutePrompt", () => {
  it("replaces $ARGUMENTS with the full arg string", () => {
    expect(substitutePrompt("Review: $ARGUMENTS", "src/a.py src/b.py")).toBe(
      "Review: src/a.py src/b.py"
    );
  });
  it("replaces positional $1 $2", () => {
    expect(substitutePrompt("Compare $1 to $2", "old new")).toBe("Compare old to new");
  });
  it("blanks unfilled positionals", () => {
    expect(substitutePrompt("X=$1 Y=$2", "only")).toBe("X=only Y=");
  });
  it("no-arg prompt is unchanged except blanked tokens", () => {
    expect(substitutePrompt("Just do it.", "")).toBe("Just do it.");
  });
});

describe("parseSlashCommand", () => {
  it("parses name and args", () => {
    expect(parseSlashCommand("/review src/a.py")).toEqual({ name: "review", args: "src/a.py" });
  });
  it("parses name with no args", () => {
    expect(parseSlashCommand("/review")).toEqual({ name: "review", args: "" });
  });
  it("returns null for non-slash text", () => {
    expect(parseSlashCommand("hello /review")).toBeNull();
  });
  it("returns null for a bare slash", () => {
    expect(parseSlashCommand("/")).toBeNull();
  });
});

describe("listPromptNames / loadPromptBody", () => {
  let dir: string;
  beforeEach(async () => {
    dir = await fsp.mkdtemp(path.join(os.tmpdir(), "prompts-"));
  });
  afterEach(async () => {
    await fsp.rm(dir, { recursive: true, force: true });
  });

  it("lists sorted *.md basenames", async () => {
    await fsp.writeFile(path.join(dir, "review.md"), "body", "utf8");
    await fsp.writeFile(path.join(dir, "ask.md"), "body", "utf8");
    await fsp.writeFile(path.join(dir, "notes.txt"), "ignore", "utf8");
    expect(await listPromptNames(dir)).toEqual(["ask", "review"]);
  });
  it("returns [] when the dir is missing", async () => {
    expect(await listPromptNames(path.join(dir, "nope"))).toEqual([]);
  });
  it("loads a body by name", async () => {
    await fsp.writeFile(path.join(dir, "review.md"), "Review $1", "utf8");
    expect(await loadPromptBody(dir, "review")).toBe("Review $1");
  });
  it("returns null for a missing prompt", async () => {
    expect(await loadPromptBody(dir, "ghost")).toBeNull();
  });
  it("rejects path-traversal names", async () => {
    expect(await loadPromptBody(dir, "../secret")).toBeNull();
  });
});
