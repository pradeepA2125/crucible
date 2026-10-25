import { expect, it, vi } from "vitest";
import { createNativeFileActions } from "../src/native-file-actions.js";
it.each(["run.command", "script.sh", "installer.pkg"])("passes %s as a document to an explicitly selected editor, never its default handler", async name => {
  const launch = vi.fn(async () => {}), reveal = vi.fn(), copy = vi.fn();
  const ports = { platform: "darwin", validateEditor: async (path: string) => path, validate: vi.fn(async () => `/project/${name}`), editor: () => "/Applications/Visual Studio Code.app", chooseEditor: vi.fn(async () => undefined), saveEditor: vi.fn(async () => {}), launch, reveal, copy };
  const action = createNativeFileActions(ports);
  await action("desktop/filesOpenEditor", name);
  expect(launch).toHaveBeenCalledWith("/usr/bin/open", ["-a", "/Applications/Visual Studio Code.app", "--", `/project/${name}`]);
  expect(reveal).not.toHaveBeenCalled(); expect(copy).not.toHaveBeenCalled();
  await action("desktop/filesReveal", name); expect(reveal).toHaveBeenCalledWith(`/project/${name}`);
});
it("uses native Reveal when no editor is selected and validates before copy or launch", async () => {
  const reveal = vi.fn(), launch = vi.fn(async () => {}), copy = vi.fn();
  const action = createNativeFileActions({ platform: "linux", validateEditor: async (path: string) => path, validate: async path => { if (path === "escape") throw new Error("Outside project"); return "/project/file"; }, editor: () => undefined, chooseEditor: async () => undefined, saveEditor: async () => {}, reveal, launch, copy });
  await action("desktop/filesOpenEditor", "file"); expect(reveal).toHaveBeenCalledWith("/project/file");
  await expect(action("desktop/filesCopyPath", "escape")).rejects.toThrow(); expect(copy).not.toHaveBeenCalled(); expect(launch).not.toHaveBeenCalled();
});
it("launches a long-lived editor without a timeout or coupling its lifetime to the host", async () => {
  const child = { once: vi.fn(), unref: vi.fn() };
  const spawn = vi.fn(() => child);
  // Import a separate isolated instance so the actual process launcher is never called.
  vi.doMock("node:child_process", () => ({ spawn, execFile: vi.fn() }));
  vi.resetModules(); const { launchEditor } = await import("../src/native-file-actions.js");
  const launched = launchEditor("/chosen/editor", ["/project/file"]);
  expect(spawn).toHaveBeenCalledWith("/chosen/editor", ["/project/file"], { detached: true, stdio: "ignore", shell: false, env: expect.any(Object) });
  const handler = child.once.mock.calls.find(call => call[0] === "spawn")![1] as () => void; handler(); await launched;
  expect(child.unref).toHaveBeenCalledOnce(); vi.doUnmock("node:child_process");
});

it("reports macOS open utility failures without coupling direct editor lifetime", async () => {
  const execFile = vi.fn((_file: string, _args: string[], _options: unknown, done: (error: Error | null) => void) => { done(new Error("missing app")); });
  vi.doMock("node:child_process", () => ({ spawn: vi.fn(), execFile })); vi.resetModules();
  const { launchEditor } = await import("../src/native-file-actions.js");
  await expect(launchEditor("/usr/bin/open", ["-a", "/Applications/missing.app", "--", "/project/file"])).rejects.toThrow("unavailable");
  expect(execFile).toHaveBeenCalledWith("/usr/bin/open", expect.any(Array), expect.objectContaining({ timeout: 15000, shell: false, env: expect.any(Object) }), expect.any(Function)); vi.doUnmock("node:child_process");
});
