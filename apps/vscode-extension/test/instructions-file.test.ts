import { describe, expect, it } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loadInstructions, saveInstructions } from "../src/instructions-file.js";

function tmpWorkspace(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "instr-"));
}

describe("instructions-file", () => {
  it("returns exists:false and empty content when AGENTS.md is missing", () => {
    expect(loadInstructions(tmpWorkspace())).toEqual({ content: "", exists: false });
  });

  it("round-trips content", () => {
    const ws = tmpWorkspace();
    saveInstructions(ws, "# Rules\nBe kind.\n");
    expect(loadInstructions(ws)).toEqual({ content: "# Rules\nBe kind.\n", exists: true });
  });
});
