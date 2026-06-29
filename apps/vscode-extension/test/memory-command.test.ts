import { describe, expect, test } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// extension.ts imports the vscode module, so it can't be loaded in this node-env vitest;
// the meaningful regression surface is the package.json contribution wiring.
describe("memory command contributions", () => {
  const pkg = JSON.parse(
    readFileSync(fileURLToPath(new URL("../package.json", import.meta.url)), "utf8")
  );

  test("contributes the openMemoryPanel command", () => {
    const cmd = pkg.contributes.commands.find(
      (c: { command: string }) => c.command === "aiEditor.openMemoryPanel"
    );
    expect(cmd).toBeTruthy();
  });

  test("command palette gates openMemoryPanel by aiEditor.memoryEnabled", () => {
    const menu = pkg.contributes.menus.commandPalette.find(
      (m: { command: string }) => m.command === "aiEditor.openMemoryPanel"
    );
    expect(menu?.when).toBe("aiEditor.memoryEnabled");
  });

  test("activates on the openMemoryPanel command", () => {
    expect(pkg.activationEvents).toContain("onCommand:aiEditor.openMemoryPanel");
  });
});
