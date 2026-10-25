import { expect, it, vi } from "vitest";
import { createFileRevealer } from "../src/file-actions.js";
it.each(["run.command", "danger.app", "script.sh", "install.pkg"])("reveals %s without invoking its OS handler", async name => {
  const shell = { showItemInFolder: vi.fn(), openPath: vi.fn() };
  await createFileRevealer(shell)(`/project/${name}`);
  expect(shell.showItemInFolder).toHaveBeenCalledWith(`/project/${name}`);
  expect(shell.openPath).not.toHaveBeenCalled();
});
