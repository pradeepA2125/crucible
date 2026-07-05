import * as fs from "node:fs";
import * as path from "node:path";

// vscode-free AGENTS.md access for the Settings "Instructions" section (the
// prompt-files.ts direct-fs pattern). The backend's ProjectInstructionsLoader
// mtime-watches the same file, so a save here is picked up on the next turn
// with no coordination.

export function loadInstructions(workspacePath: string): { content: string; exists: boolean } {
  try {
    return { content: fs.readFileSync(path.join(workspacePath, "AGENTS.md"), "utf8"), exists: true };
  } catch {
    return { content: "", exists: false };
  }
}

export function saveInstructions(workspacePath: string, content: string): void {
  fs.writeFileSync(path.join(workspacePath, "AGENTS.md"), content, "utf8");
}
